"""Things to do once: load the work on screen again, go back to the default playlist, restart the device."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry
from .entity import Ff1Entity

BUTTONS = {"refresh_artwork": "refresh", "default_playlist": "show_default", "reboot": "reboot"}


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    add(Ff1Button(entry.runtime_data, key) for key in BUTTONS)


class Ff1Button(Ff1Entity, ButtonEntity):
    platform_name = "button"
    needs_control = True

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        if key == "reboot":
            self._attr_device_class = ButtonDeviceClass.RESTART
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.act(getattr(self.coordinator.client, BUTTONS[self.key])())
