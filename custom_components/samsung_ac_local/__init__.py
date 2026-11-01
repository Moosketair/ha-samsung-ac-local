"""
Samsung AC Local integration.

Controls Samsung OCF air conditioners locally via DTLS/CoAP,
bypassing the Samsung SmartThings cloud.
"""

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .api import SamsungACClient, SamsungACConnectionError
from .cert import CERT_PEM, KEY_PEM
from .config_flow import (
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_POWER_SENSOR,
    CONF_PUBLISH_HVAC_ACTION,
)
from .const import LOGGER
from .coordinator import DEFAULT_POLL_INTERVAL, SamsungACCoordinator
from .data import SamsungACConfigEntry, SamsungACData
from .power_monitor import SamsungACPowerMonitor

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: SamsungACConfigEntry) -> bool:
    """
    Set up Samsung AC Local from a config entry.

    Establishes the DTLS connection using the bundled certificate,
    fetches device info, and starts the polling coordinator.
    """
    host = entry.data[CONF_HOST]
    client = SamsungACClient(host, CERT_PEM, KEY_PEM)
    try:
        await hass.async_add_executor_job(client.connect)
    except SamsungACConnectionError as err:
        await hass.async_add_executor_job(client.disconnect)
        msg = f"Failed to connect to Samsung AC at {host}"
        raise ConfigEntryNotReady(msg) from err

    device_info = await hass.async_add_executor_job(client.get_device_info)
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    coordinator = SamsungACCoordinator(hass, client, poll_interval)
    await coordinator.async_config_entry_first_refresh()

    power_monitor = None
    if power_sensor := entry.options.get(CONF_POWER_SENSOR):
        power_monitor = SamsungACPowerMonitor(
            hass, coordinator, power_sensor, entry.entry_id
        )
        await power_monitor.async_start()

    entry.runtime_data = SamsungACData(
        client=client,
        coordinator=coordinator,
        device_info=device_info,
        power_monitor=power_monitor,
        publish_power_hvac_action=bool(entry.options.get(CONF_PUBLISH_HVAC_ACTION, False)),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SamsungACConfigEntry) -> bool:
    """Unload a config entry and disconnect from the AC."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if entry.runtime_data.power_monitor is not None:
            await entry.runtime_data.power_monitor.async_stop()
        await hass.async_add_executor_job(entry.runtime_data.client.disconnect)

    return unloaded


async def _async_options_updated(
    hass: HomeAssistant, entry: SamsungACConfigEntry
) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Migrate config entry to a new version.

    v1 -> v2: Removed cert_path and key_path (now using bundled certificate).
    """
    if entry.version == 1:
        LOGGER.debug("Migrating config entry from version 1 to 2")
        new_data = {CONF_HOST: entry.data[CONF_HOST]}
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)

    return True
