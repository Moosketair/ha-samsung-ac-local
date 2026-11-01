"""Optional entities for external whole-AC power and adaptive activity inference.

These entities never send commands or assert a measured compressor state.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import callback

from .const import DOMAIN

if TYPE_CHECKING:
    from .power_monitor import SamsungACPowerMonitor


class SamsungACExternalPowerSensor(SensorEntity):
    """Surface chosen whole-AC electrical power, including both units."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "external_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:flash"

    def __init__(
        self, monitor: SamsungACPowerMonitor, device_id: str
    ) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{device_id}_external_power"
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id)}}
        self._unsub_monitor = None

    @property
    def available(self) -> bool:
        return self._monitor.available

    @property
    def native_value(self) -> float | None:
        return self._monitor.model.last_power_w

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source_entity_id": self._monitor.sensor_entity_id}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_monitor = self._monitor.add_listener(self._on_model_update)
        self.async_on_remove(self._unsub_monitor)

    @callback
    def _on_model_update(self) -> None:
        self.async_write_ha_state()


class SamsungACEstimatedActivitySensor(SensorEntity):
    """Conservative estimate from consumption and the Samsung HVAC mode."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "estimated_activity"
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(
        self, monitor: SamsungACPowerMonitor, device_id: str
    ) -> None:
        self._monitor = monitor
        self._attr_unique_id = f"{device_id}_estimated_activity"
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id)}}
        self._unsub_monitor = None

    @property
    def available(self) -> bool:
        return self._monitor.available

    @property
    def native_value(self) -> str:
        return self._monitor.model.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        model = self._monitor.model
        return {
            "confidence": model.confidence,
            "power_w": model.last_power_w,
            "reference_power_w": model.reference_w,
            "activation_threshold_w": model.activation_threshold_w,
            "sample_counts": model.sample_counts,
            "operating_mode": model.mode,
            "source_entity_id": self._monitor.sensor_entity_id,
            "note": "Statistical estimate, not a measured compressor status",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_monitor = self._monitor.add_listener(self._on_model_update)
        self.async_on_remove(self._unsub_monitor)

    @callback
    def _on_model_update(self) -> None:
        self.async_write_ha_state()
