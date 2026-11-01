"""Optional read-only adaptive estimate from an external whole-AC power sensor.

The sensor includes both indoor and outdoor units. Power alone cannot measure
compressor status; this module never controls the appliance or claims certainty.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import ACStatus
from .const import DOMAIN, HvacMode, LOGGER, Power
from .coordinator import SamsungACCoordinator

_MIN_SAMPLE_SPACING = 25  # seconds; do not learn from duplicate poll values
_MODE_SETTLE_TIME = 120  # seconds after a Samsung mode change
_SENSOR_MAX_AGE = 900  # stale data is not real-time activity
_MIN_REFERENCE_SAMPLES = 6
_STORAGE_VERSION = 1


def _percentile(values: list[float], fraction: float) -> float:
    """Return an interpolated percentile without additional dependencies."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Empty sample")
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class AdaptivePowerModel:
    """Conservative per-device learning; no assumptions of fixed watt thresholds."""

    def __init__(self) -> None:
        self.off: deque[float] = deque(maxlen=90)
        self.fan: deque[float] = deque(maxlen=90)
        self.heat: deque[float] = deque(maxlen=240)
        self.cool: deque[float] = deque(maxlen=240)
        self.mode: str | None = None
        self.mode_since: float = 0.0
        self.last_sample: float = 0.0
        self._last_reported: float = 0.0
        self.last_power_w: float | None = None
        self.state = "learning"
        self.confidence = "none"
        self.reference_w: float | None = None
        self.activation_threshold_w: float | None = None
        self._candidate: str | None = None
        self._votes = 0

    @classmethod
    def from_storage(cls, data: Any) -> AdaptivePowerModel:
        """Restore learned samples only; restart votes are never trusted."""
        model = cls()
        if not isinstance(data, dict):
            return model
        for name in ("off", "fan", "heat", "cool"):
            values = data.get(name, [])
            if not isinstance(values, list):
                continue
            queue = getattr(model, name)
            for value in values[-queue.maxlen :]:
                if (
                    isinstance(value, (float, int))
                    and math.isfinite(value)
                    and 0 <= value <= 25000
                ):
                    queue.append(float(value))
        return model

    def as_storage(self) -> dict[str, list[float]]:
        return {name: list(getattr(self, name)) for name in ("off", "fan", "heat", "cool")}

    @property
    def sample_counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in ("off", "fan", "heat", "cool")}

    def _reference(self) -> tuple[float | None, float]:
        """Prefer learned FAN baseline, including indoor airflow, over OFF baseline."""
        data = self.fan if len(self.fan) >= _MIN_REFERENCE_SAMPLES else self.off
        if len(data) < _MIN_REFERENCE_SAMPLES:
            return None, 0.0
        values = list(data)
        median = statistics.median(values)
        mad = statistics.median(abs(x - median) for x in values)
        return float(median), float(mad)

    def _fallback_reference(self, mode: str) -> tuple[float | None, float]:
        """Learn a low-power cluster but NEVER label it compressor-off.

        Used only if the PAC has not been seen powered off or in fan-only.
        A wide spread is not enough to infer real compressor status; the
        high cluster can at most be marked probable thermal activity.
        """
        values = list(self.heat if mode == "heat" else self.cool)
        if len(values) < 30:
            return None, 0
        low = _percentile(values, 0.10)
        high = _percentile(values, 0.75)
        if high - low < 300 or high < max(2.5 * low, 400):
            return None, 0
        return low, 0

    def observe(
        self, power_w: float, mode: str | None, timestamp: float,
        reported_at: float | None = None,
    ) -> bool:
        """Observe one timed reading. Return True if the public state changed."""
        if not math.isfinite(power_w) or power_w < 0 or power_w > 25000:
            return False
        original = (self.state, self.confidence, self.reference_w, self.activation_threshold_w)
        self.last_power_w = power_w
        if mode is None:
            self.state, self.confidence = "unknown", "none"
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)

        if mode != self.mode:
            self.mode = mode
            self.mode_since = timestamp
            self._candidate = None
            self._votes = 0
            if mode == "off":
                self.state, self.confidence = "off", "high"
            elif mode == "fan":
                self.state, self.confidence = "fan", "medium"
            else:
                self.state, self.confidence = "learning", "none"

        # The coordinator may poll faster than the meter reports. Never
        # learn multiple samples from one cached source-state value.
        if reported_at is not None:
            if reported_at <= self._last_reported:
                return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)
            self._last_reported = reported_at
            # Data recorded before a mode switch cannot train the new mode.
            if reported_at < self.mode_since:
                return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)

        if timestamp < self.last_sample + _MIN_SAMPLE_SPACING:
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)
        self.last_sample = timestamp

        # Skip transition consumption (compressor coast-down and warm-up).
        if timestamp - self.mode_since < _MODE_SETTLE_TIME:
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)

        if mode in ("off", "fan", "heat", "cool"):
            getattr(self, mode).append(power_w)

        if mode == "off":
            self.state, self.confidence = "off", "high"
            self.reference_w, _ = self._reference()
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)
        if mode == "fan":
            self.state, self.confidence = "fan", "medium"
            self.reference_w, _ = self._reference()
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)
        if mode not in ("heat", "cool"):
            self.state, self.confidence = "unknown", "none"
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)

        ref, mad = self._reference()
        trusted_reference = ref is not None
        if ref is None:
            ref, mad = self._fallback_reference(mode)

        self.reference_w = ref
        if ref is None:
            self.activation_threshold_w = None
            self.state, self.confidence = "learning", "none"
            self._votes = 0
            self._candidate = None
            return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)

        # Thresholds adapt to standby/fan power and observed variation; they
        # cannot distinguish low-output inverter heating from indoor fan load.
        # If no fan-only baseline exists, OFF consumption omits indoor
        # fan power. Keep a generous minimum margin so a running indoor
        # blower alone is less likely to be mistaken for a compressor.
        has_fan_reference = len(self.fan) >= _MIN_REFERENCE_SAMPLES
        margin = max(110.0 if has_fan_reference else 250.0, ref * 0.8, mad * 5.0)
        if not trusted_reference:
            margin = max(300.0, margin)
        activation = ref + margin
        idle_ceiling = ref + max(35.0, ref * 0.30, mad * 3.0)
        self.activation_threshold_w = round(activation, 1)

        if power_w >= activation:
            candidate = "heating_probable" if mode == "heat" else "cooling_probable"
        elif trusted_reference and power_w <= idle_ceiling:
            candidate = "idle_probable"
        else:
            candidate = "uncertain"

        if candidate == self._candidate:
            self._votes += 1
        else:
            self._candidate, self._votes = candidate, 1

        # Require multiple independent observations before changing activity.
        if self._votes >= 2:
            self.state = candidate
            if candidate == "uncertain":
                self.confidence = "low"
            elif not trusted_reference:
                self.confidence = "low"
            elif len(self.off) + len(self.fan) >= 18 and self._votes >= 4:
                self.confidence = "high" if candidate != "idle_probable" else "medium"
            else:
                self.confidence = "medium"
        return original != (self.state, self.confidence, self.reference_w, self.activation_threshold_w)


class SamsungACPowerMonitor:
    """Listen to one optional HA sensor and persist learned power profiles."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SamsungACCoordinator,
        sensor_entity_id: str,
        entry_id: str,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.sensor_entity_id = sensor_entity_id
        self._store = Store(hass, _STORAGE_VERSION, f"{DOMAIN}_power_{entry_id}")
        self.model = AdaptivePowerModel()
        self._listeners: list[Callable[[], None]] = []
        self._unsub_sensor: Callable[[], None] | None = None
        self._unsub_coordinator: Callable[[], None] | None = None
        self._data_valid = False
        self._last_reported_at: float | None = None
        self._next_save = 0.0

    async def async_start(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict) and data.get("sensor_entity_id") == self.sensor_entity_id:
            self.model = AdaptivePowerModel.from_storage(data.get("model"))

        self._unsub_sensor = async_track_state_change_event(
            self.hass, [self.sensor_entity_id], self._handle_sensor_event
        )
        self._unsub_coordinator = self.coordinator.async_add_listener(self._sample_current)
        self._sample_current()

    @callback
    def _handle_sensor_event(self, event: Event) -> None:
        self._sample(event.data.get("new_state"))

    @callback
    def _sample_current(self) -> None:
        self._sample(self.hass.states.get(self.sensor_entity_id))

    @staticmethod
    def _mode(status: ACStatus | None) -> str | None:
        if status is None:
            return None
        if status.power == Power.OFF:
            return "off"
        if status.power != Power.ON or status.hvac_mode is None:
            return None
        return {
            HvacMode.HEAT: "heat",
            HvacMode.COOL: "cool",
            HvacMode.FAN: "fan",
        }.get(status.hvac_mode, "other")

    @callback
    def _sample(self, state: State | None) -> None:
        now = dt_util.utcnow().timestamp()
        unit = state.attributes.get("unit_of_measurement") if state else None
        try:
            watts = float(state.state) if state else None
        except (TypeError, ValueError):
            watts = None
        if unit == "kW" and watts is not None:
            watts *= 1000
        reported = getattr(state, "last_reported", state.last_updated) if state else None
        reported_at = reported.timestamp() if reported else None
        valid = (
            watts is not None
            and unit in ("W", "kW")
            and math.isfinite(watts)
            and 0 <= watts <= 25000
            and state is not None
            and reported_at is not None
            and (now - reported_at) <= _SENSOR_MAX_AGE
        )
        if not valid:
            if self._data_valid:
                self._data_valid = False
                self._notify()
            return

        # This observer is read-only; it never sends an appliance command.
        was_valid = self._data_valid
        self._data_valid = True
        self._last_reported_at = reported_at
        before = self.model.sample_counts
        previous_watts = self.model.last_power_w
        changed = self.model.observe(
            watts, self._mode(self.coordinator.data), now, reported_at=reported_at
        )
        if (
            changed
            or not was_valid
            or previous_watts != watts
            or before != self.model.sample_counts
        ):
            self._notify()
        if before != self.model.sample_counts and now >= self._next_save:
            self._next_save = now + 300
            self._store.async_delay_save(self._storage_data, 30)

    def _storage_data(self) -> dict[str, Any]:
        return {
            "sensor_entity_id": self.sensor_entity_id,
            "model": self.model.as_storage(),
        }

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @property
    def available(self) -> bool:
        if not self._data_valid or self.model.last_power_w is None:
            return False
        if (
            self._last_reported_at is None
            or dt_util.utcnow().timestamp() - self._last_reported_at > _SENSOR_MAX_AGE
        ):
            return False
        return True

    async def async_stop(self) -> None:
        if self._unsub_sensor:
            self._unsub_sensor()
            self._unsub_sensor = None
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        await self._store.async_save(self._storage_data())
        self._listeners.clear()
