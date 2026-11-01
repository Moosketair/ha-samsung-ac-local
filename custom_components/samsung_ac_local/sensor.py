"""
Sensor platform for the Samsung AC Local integration.

Exposes read-only measurements: outdoor temperature, energy consumption,
filter usage, WiFi signal strength, operation count, auto-clean progress,
freeze wash progress, and more.
"""

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamsungACCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .api import ACStatus
    from .data import SamsungACConfigEntry, SamsungACData

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    SensorEntityDescription(
        key="energy_wh",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    SensorEntityDescription(
        key="filter_usage_hours",
        translation_key="filter_usage",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="duration_on_minutes",
        translation_key="duration_on",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="auto_clean_progress",
        translation_key="auto_clean_progress",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="freeze_wash_progress",
        translation_key="freeze_wash_progress",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="filter_capacity_hours",
        translation_key="filter_capacity",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="filter_status",
        translation_key="filter_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:air-filter",
    ),
    SensorEntityDescription(
        key="sleep_timer_minutes",
        translation_key="sleep_timer",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    SensorEntityDescription(
        key="ai_temperature",
        translation_key="ai_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    SensorEntityDescription(
        key="operation_count",
        translation_key="operation_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="ai_sleep_elapsed_minutes",
        translation_key="ai_sleep_elapsed",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
)


async def async_setup_entry(
    _hass: object,
    entry: SamsungACConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Samsung AC sensor entities."""
    data: SamsungACData = entry.runtime_data
    device_id = data.device_info.mac_wifi or data.client.host
    async_add_entities(
        SamsungACSensor(data.coordinator, description, device_id)
        for description in SENSORS
    )


class SamsungACSensor(CoordinatorEntity[SamsungACCoordinator], SensorEntity):
    """Sensor entity for Samsung AC Local."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SamsungACCoordinator,
        description: SensorEntityDescription,
        device_id: str,
    ) -> None:
        """
        Initialize the sensor.

        Args:
            coordinator: The data update coordinator.
            description: Entity description defining the sensor type.
            device_id: Device identifier (MAC or host).

        """
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id)}}

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor value from coordinator data."""
        status: ACStatus | None = self.coordinator.data
        if status is None:
            return None

        return getattr(status, self.entity_description.key, None)
