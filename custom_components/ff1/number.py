"""The screen's own brightness, contrast and volume, over DDC. Only what the panel answered at setup is offered:
panels differ, and one that does not speak DDC gets none of these."""

from __future__ import annotations

from dataclasses import replace

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry
from .entity import Ff1Entity

PANEL = ("brightness", "contrast", "volume")


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    panel = entry.runtime_data.data.panel
    add(Ff1PanelNumber(entry.runtime_data, a) for a in PANEL if panel and getattr(panel, a) is not None)


class Ff1PanelNumber(Ff1Entity, NumberEntity):
    platform_name = "number"
    needs_control = True
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, action: str) -> None:
        super().__init__(coordinator, f"panel_{action}")
        self.action = action

    @property
    def native_value(self) -> float | None:
        return getattr(self.data.panel, self.action) if self.data.panel else None

    async def async_set_native_value(self, value: float) -> None:
        client = self.coordinator.client

        async def run() -> None:
            await client.panel_set(self.action, int(value))
            self.coordinator.async_set_updated_data(replace(self.data, panel=await client.panel()))
        await self.coordinator.act(run())
