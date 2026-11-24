"""Config flow for the Sikker Hjem integration."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PORT
from homeassistant.core import callback

from .const import (
    CONF_ACCOUNT,
    CONF_DELAYED_SENSORS,
    CONF_ENTRY_DELAY,
    DEFAULT_ENTRY_DELAY,
    DEFAULT_PORT,
    DOMAIN,
    LISTEN_HOST,
)

_ACCOUNT_RE = re.compile(r"^[0-9A-Fa-f]{3,16}$")

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_ACCOUNT, default=""): str,
    }
)


async def _async_port_available(port: int) -> bool:
    """Return True if we can listen on the port."""
    loop = asyncio.get_running_loop()
    try:
        server = await loop.create_server(asyncio.Protocol, LISTEN_HOST, port)
    except OSError:
        return False
    server.close()
    await server.wait_closed()
    return True


class SikkerHjemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sikker Hjem."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return SikkerHjemOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the listening port and (optional) account number."""
        errors: dict[str, str] = {}
        if user_input is not None:
            port: int = user_input[CONF_PORT]
            account = user_input.get(CONF_ACCOUNT, "").strip()
            self._async_abort_entries_match({CONF_PORT: port})

            if account and not _ACCOUNT_RE.match(account):
                errors[CONF_ACCOUNT] = "invalid_account"
            elif not await _async_port_available(port):
                errors[CONF_PORT] = "port_in_use"
            else:
                await self.async_set_unique_id(f"port_{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Sikker Hjem (port {port})",
                    data={CONF_PORT: port, CONF_ACCOUNT: account},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )


class SikkerHjemOptionsFlow(OptionsFlow):
    """Entry delay handling options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the entry delay and which sensors are delayed."""
        if user_input is not None:
            user_input[CONF_DELAYED_SENSORS] = ", ".join(
                name.strip()
                for name in user_input.get(CONF_DELAYED_SENSORS, "").split(",")
                if name.strip()
            )
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENTRY_DELAY, default=DEFAULT_ENTRY_DELAY
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=600)),
                vol.Optional(CONF_DELAYED_SENSORS, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
