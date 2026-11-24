"""One device per FF1."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Ff1Coordinator, Ff1Data


def slug(device_id: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", device_id.lower()).strip("_")
    return s if s.startswith("ff1_") else f"ff1_{s}"


class Ff1Entity(CoordinatorEntity[Ff1Coordinator]):
    """Entity ids are fixed (`<platform>.<device id>_<key>`, as `sensor.ff1_abcd1234_cpu_temperature`), whatever
    Home Assistant's language: other programs (agentic-assistant's gateway and watcher) find them by that name."""

    _attr_has_entity_name = True
    platform_name = "sensor"
    needs_control = False                     # True: unavailable while the device refuses this network's commands

    def __init__(self, coordinator: Ff1Coordinator, key: str) -> None:
        super().__init__(coordinator)
        device_id = coordinator.config_entry.unique_id
        self.entity_id = f"{self.platform_name}.{slug(device_id)}_{key}"
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_translation_key = key
        data = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)}, name=(data.device.name if data.device else "") or device_id,
            manufacturer="Feral File", model="FF1", serial_number=device_id, sw_version=data.status.version or None,
            configuration_url=coordinator.client.base + "/api/v2/status")

    @property
    def data(self) -> Ff1Data:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return super().available and (self.data.controllable or not self.needs_control)
