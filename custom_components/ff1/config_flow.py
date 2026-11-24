"""Setting up an FF1: its address on the home network. Nothing else: the device asks no login of its own LAN.

One form, so a script (agentic-assistant's steward) can fill it as a person would. The address is tried at
once; what answers must be an FF1, and the same FF1 is never added twice (its device id is the entry's id).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ff1_lan import Ff1Client, Ff1Error, NotLocal

from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
})


class Ff1ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), user_input.get(CONF_PORT, DEFAULT_PORT)
            if not host or any(c in host for c in "/ @?#"):
                errors["base"] = "invalid_host"
            else:
                try:
                    status = await Ff1Client(async_get_clientsession(self.hass), host, port).status()
                except NotLocal:
                    errors["base"] = "not_local"
                except Ff1Error:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(status.device_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                    return self.async_create_entry(title=status.device_id, data={CONF_HOST: host, CONF_PORT: port})
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)
