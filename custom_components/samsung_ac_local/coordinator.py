"""
DataUpdateCoordinator for the Samsung AC Local integration.

Wraps the blocking SamsungACClient.get_status() call in an executor job
and provides the ACStatus dataclass to all subscribed entities.
"""

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ACStatus,
    SamsungACClient,
    SamsungACConnectionError,
    SamsungACRequestError,
)
from .const import LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

DEFAULT_POLL_INTERVAL = 30


class SamsungACCoordinator(DataUpdateCoordinator[ACStatus]):
    """
    Coordinator for Samsung AC status polling.

    Calls the blocking DTLS/CoAP client in an executor thread and
    distributes the resulting ACStatus to all entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: SamsungACClient,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """
        Initialize the coordinator.

        Args:
            hass: Home Assistant instance.
            client: Connected SamsungACClient instance.
            poll_interval: Polling interval in seconds.

        """
        super().__init__(
            hass,
            LOGGER,
            name=f"Samsung AC ({client.host})",
            update_interval=timedelta(seconds=poll_interval),
            always_update=False,
        )
        self.client = client

    @callback
    def async_update_optimistic(self, **kwargs: Any) -> None:
        """
        Optimistically update status fields and notify all entities.

        Called after the AC confirms a command to immediately reflect
        the change in the UI. Also resets the poll timer to prevent a
        scheduled poll from overwriting with stale data.

        Args:
            **kwargs: ACStatus field names and their new values.

        """
        self.async_set_updated_data(replace(self.data, **kwargs))

    async def _async_update_data(self) -> ACStatus:
        """Fetch AC status via the blocking DTLS client."""
        try:
            status = await self.hass.async_add_executor_job(self.client.get_status)
            # Supported modes are device capabilities, not a live state reading.
            # A transient failed mode GET must not remove 'heat'/'cool' from
            # the Home Assistant entity and block subsequent HVAC commands.
            previous = self.data
            if previous is not None and not status.supported_modes:
                status = replace(status, supported_modes=previous.supported_modes)
            return status
        except (SamsungACConnectionError, SamsungACRequestError) as err:
            msg = f"Error communicating with AC: {err}"
            raise UpdateFailed(msg) from err
