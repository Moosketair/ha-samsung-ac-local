"""
Runtime data types for the Samsung AC Local integration.

Defines the typed ConfigEntry alias and the runtime data structure
stored in ``entry.runtime_data`` during the config entry lifecycle.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import DeviceInfo, SamsungACClient
    from .coordinator import SamsungACCoordinator
    from .power_monitor import SamsungACPowerMonitor

type SamsungACConfigEntry = ConfigEntry[SamsungACData]


@dataclass
class SamsungACData:
    """
    Runtime data for a Samsung AC config entry.

    Created during entry setup and available via ``entry.runtime_data``.
    Cleaned up automatically on unload.
    """

    client: SamsungACClient
    coordinator: SamsungACCoordinator
    device_info: DeviceInfo
    power_monitor: SamsungACPowerMonitor | None = None
