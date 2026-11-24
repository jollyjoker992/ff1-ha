"""Feral File FF1: the art computer on the wall, over its LAN interface (port 1111). No account, no key."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import Ff1ConfigEntry, Ff1Coordinator

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SWITCH,
             Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry) -> bool:
    coordinator = Ff1Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.listen()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Ff1ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
