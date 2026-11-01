"""
Config flow for the Samsung AC Local integration.

Handles initial setup (host only) and options flow
(poll interval) for post-setup configuration changes.
"""

from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .api import SamsungACClient, SamsungACConnectionError
from .cert import CERT_PEM, KEY_PEM
from .const import DOMAIN, LOGGER
from .coordinator import DEFAULT_POLL_INTERVAL

CONF_HOST = "host"
CONF_POLL_INTERVAL = "poll_interval"
CONF_POWER_SENSOR = "power_sensor"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


class SamsungACLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Samsung AC Local."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return SamsungACOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """
        Handle the user setup step.

        Asks for the AC host IP address, then validates the connection
        with a DTLS handshake using the bundled certificate.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self.hass.async_add_executor_job(
                self._validate_input, user_input
            )
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Samsung AC ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    def _validate_input(self, user_input: dict[str, Any]) -> str | None:
        """
        Validate the user input by attempting a DTLS connection.

        Uses the bundled certificate with the Samsung cloud UUID.
        Returns an error key on failure, None on success.
        """
        client = SamsungACClient(user_input[CONF_HOST], CERT_PEM, KEY_PEM)
        try:
            client.connect()
        except SamsungACConnectionError:
            LOGGER.debug(
                "Connection test to %s failed", user_input[CONF_HOST], exc_info=True
            )
            return "cannot_connect"
        finally:
            client.disconnect()

        return None


class SamsungACOptionsFlow(OptionsFlow):
    """Options flow for poll interval and optional whole-AC power source."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """
        Initialize the options flow.

        Args:
            config_entry: The config entry to modify options for.

        """
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )

        current_sensor = self._config_entry.options.get(CONF_POWER_SENSOR)
        fields = {
            vol.Required(CONF_POLL_INTERVAL, default=current_interval): vol.All(
                int, vol.Range(min=5, max=300)
            ),
        }
        power_key = (
            vol.Optional(CONF_POWER_SENSOR, default=current_sensor)
            if current_sensor
            else vol.Optional(CONF_POWER_SENSOR)
        )
        fields[power_key] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
        )
