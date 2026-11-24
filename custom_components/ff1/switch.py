"""The sleep schedule: on, the screen goes dark at the sleep time and comes back at the wake time by itself."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry
from .entity import Ff1Entity


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    add([Ff1SleepSchedule(entry.runtime_data, "sleep_schedule")])


class Ff1SleepSchedule(Ff1Entity, SwitchEntity):
    platform_name = "switch"
    needs_control = True
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def _schedule(self):
        return self.data.device.sleep if self.data.device else None

    @property
    def is_on(self) -> bool | None:
        return self._schedule.enabled if self._schedule else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._schedule
        return {"sleep_time": s.sleep_time, "wake_time": s.wake_time, "days": list(s.days) or "every day"} if s else {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.act(self.coordinator.client.set_sleep_schedule(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.act(self.coordinator.client.set_sleep_schedule(False))
