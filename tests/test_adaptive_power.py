"""Regression tests for optional self-learning whole-AC electrical activity.

These tests use a synthetic meter; they do not contact or control a Samsung AC.
"""
from types import SimpleNamespace

from homeassistant.components.climate.const import HVACAction, HVACMode

from custom_components.samsung_ac_local.climate import SamsungACClimateEntity
from custom_components.samsung_ac_local.power_monitor import AdaptivePowerModel


def _train_off(model: AdaptivePowerModel, count: int = 20, start: float = 100) -> float:
    """Use isolated fresh off-meter reports, then return current time."""
    model.observe(12, "off", start, reported_at=start)
    timestamp = start + 130.0
    for _ in range(count):
        model.observe(12, "off", timestamp, reported_at=timestamp)
        timestamp += 30
    return timestamp


def _train_heat(model: AdaptivePowerModel, now: float, watts: float) -> None:
    model.observe(watts, "heat", now, reported_at=now)
    for index in range(4):
        t = now + 130 + 30 * index
        model.observe(watts, "heat", t, reported_at=t)


def test_learning_requires_off_reference_and_repeated_heat_readings() -> None:
    model = AdaptivePowerModel()
    _train_heat(model, 100, 850)
    assert model.state == "learning"
    assert model.confidence == "none"

    start = _train_off(model, start=500)
    _train_heat(model, start + 100, 850)
    assert model.reference_w == 12
    assert model.activation_threshold_w >= 262
    assert model.state == "heating_probable"
    assert model.confidence == "high"


def test_low_power_is_not_claimed_as_real_compressor_off() -> None:
    model = AdaptivePowerModel()
    start = _train_off(model)
    _train_heat(model, start + 100, 125)
    assert model.state in ("idle_probable", "uncertain")
    assert model.confidence != "high"


def test_duplicate_meter_reports_do_not_train_twice() -> None:
    model = AdaptivePowerModel()
    model.observe(15, "off", 100, reported_at=100)
    model.observe(15, "off", 230, reported_at=230)
    assert model.sample_counts["off"] == 1
    model.observe(15, "off", 280, reported_at=230)
    assert model.sample_counts["off"] == 1
    model.observe(15, "off", 310, reported_at=310)
    assert model.sample_counts["off"] == 2


def test_old_report_cannot_train_new_mode() -> None:
    model = AdaptivePowerModel()
    model.observe(12, "off", 100, reported_at=100)
    model.observe(12, "off", 240, reported_at=240)
    model.observe(900, "heat", 300, reported_at=240)
    model.observe(900, "heat", 450, reported_at=240)
    assert model.sample_counts["heat"] == 0
    model.observe(900, "heat", 480, reported_at=480)
    assert model.sample_counts["heat"] == 1


def test_persist_and_reload_learning_without_reusing_transient_state() -> None:
    model = AdaptivePowerModel()
    start = _train_off(model)
    _train_heat(model, start + 100, 850)
    restored = AdaptivePowerModel.from_storage(model.as_storage())
    assert restored.sample_counts == model.sample_counts
    assert restored.state == "learning"
    assert restored.confidence == "none"


def _climate(mode: HVACMode, activity: str, confidence: str, enabled: bool = True):
    """Create only the minimal state needed to verify the passive property."""
    entity = SamsungACClimateEntity.__new__(SamsungACClimateEntity)
    entity._attr_hvac_mode = mode
    entity._publish_power_hvac_action = enabled
    entity._power_monitor = SimpleNamespace(
        available=True,
        model=SimpleNamespace(state=activity, confidence=confidence),
    )
    return entity


def test_hvac_action_opt_in_and_high_confidence_only() -> None:
    confident = _climate(HVACMode.HEAT, "heating_probable", "high")
    assert confident.hvac_action == HVACAction.HEATING
    confident._attr_hvac_mode = HVACMode.COOL
    assert confident.hvac_action is None

    cooling = _climate(HVACMode.COOL, "cooling_probable", "high")
    assert cooling.hvac_action == HVACAction.COOLING

    uncertain = _climate(HVACMode.HEAT, "heating_probable", "low")
    assert uncertain.hvac_action is None

    idle = _climate(HVACMode.HEAT, "idle_probable", "medium")
    assert idle.hvac_action is None

    disabled = _climate(HVACMode.HEAT, "heating_probable", "high", False)
    assert disabled.hvac_action is None
    confident._attr_hvac_mode = HVACMode.OFF
    assert confident.hvac_action == HVACAction.OFF


def test_power_sensor_unavailable_is_not_hvac_evidence() -> None:
    climate = _climate(HVACMode.HEAT, "heating_probable", "high")
    climate._power_monitor.available = False
    assert climate.hvac_action is None
