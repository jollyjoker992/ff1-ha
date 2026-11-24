"""Yes/no facts of the FF1: online, reachable from outside, asleep, an update waiting, a work that fails."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass, BinarySensorEntity,
                                                    BinarySensorEntityDescription)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry, Ff1Data
from .entity import Ff1Entity


@dataclass(frozen=True, kw_only=True)
class Ff1Binary(BinarySensorEntityDescription):
    value: Callable[[Ff1Data], bool | None]


def _problem(d: Ff1Data) -> bool | None:
    """A work of the playlist is broken, or the one on screen failed to render."""
    if d.player is None and d.health is None:
        return None
    return bool((d.health and d.health.broken) or (d.player and d.player.render == "failed" and not d.sleeping))


DIAG = EntityCategory.DIAGNOSTIC
BINARY: tuple[Ff1Binary, ...] = (
    Ff1Binary(key="internet", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=DIAG,
              value=lambda d: d.status.internet),
    Ff1Binary(key="relay", device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=DIAG,
              value=lambda d: d.status.relay_connected),
    Ff1Binary(key="lan_control", entity_category=DIAG, value=lambda d: d.status.lan_open),
    Ff1Binary(key="sleeping", value=lambda d: d.sleeping),
    Ff1Binary(key="update_available", device_class=BinarySensorDeviceClass.UPDATE, entity_category=DIAG,
              value=lambda d: d.device.update_available if d.device else None),
    Ff1Binary(key="media_problem", device_class=BinarySensorDeviceClass.PROBLEM, value=_problem),
)


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    add(Ff1BinaryEntity(entry.runtime_data, b) for b in BINARY)


class Ff1BinaryEntity(Ff1Entity, BinarySensorEntity):
    platform_name = "binary_sensor"
    entity_description: Ff1Binary

    def __init__(self, coordinator, description: Ff1Binary) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.data)
