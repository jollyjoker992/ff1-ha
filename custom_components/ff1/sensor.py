"""What the FF1 reports about the art on screen and about itself."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry, Ff1Data
from .entity import Ff1Entity


@dataclass(frozen=True, kw_only=True)
class Ff1Sensor(SensorEntityDescription):
    value: Callable[[Ff1Data], Any]
    attrs: Callable[[Ff1Data], dict[str, Any]] | None = None


def _now(d: Ff1Data):
    return d.player.now if d.player and not d.sleeping else None


def _boot(d: Ff1Data):
    m = d.metrics
    if not m or m.uptime_s is None or m.at is None:
        return None
    return (m.at - timedelta(seconds=m.uptime_s)).replace(second=0, microsecond=0)     # steady to the minute


DIAG = EntityCategory.DIAGNOSTIC
TEMP = {"device_class": SensorDeviceClass.TEMPERATURE, "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT, "suggested_display_precision": 0, "entity_category": DIAG}
PCT = {"native_unit_of_measurement": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT,
       "suggested_display_precision": 0, "entity_category": DIAG}

SENSORS: tuple[Ff1Sensor, ...] = (
    Ff1Sensor(key="now_showing", value=lambda d: (_now(d).title or None) if _now(d) else None,
              attrs=lambda d: {"artist": _now(d).artist if _now(d) else None,
                               "position": d.player.index + 1 if d.player and d.player.index is not None else None,
                               "works": len(d.player.works) if d.player else None,
                               "render": d.player.render if d.player else None}),
    Ff1Sensor(key="firmware", entity_category=DIAG, value=lambda d: d.status.version or None,
              attrs=lambda d: {"latest": d.device.latest if d.device else None, "branch": d.status.branch}),
    Ff1Sensor(key="cpu_temperature", value=lambda d: d.metrics.cpu_temp if d.metrics else None, **TEMP),
    Ff1Sensor(key="gpu_temperature", value=lambda d: d.metrics.gpu_temp if d.metrics else None, **TEMP),
    Ff1Sensor(key="memory_used", value=lambda d: d.metrics.memory_used_pct if d.metrics else None, **PCT),
    Ff1Sensor(key="disk_used", value=lambda d: d.metrics.disk_used_pct if d.metrics else None, **PCT,
              attrs=lambda d: {"free_gb": d.metrics.disk_free_gb if d.metrics else None}),
    Ff1Sensor(key="wifi_signal", value=lambda d: d.wifi_signal, **PCT),
    Ff1Sensor(key="cache_used", value=lambda d: d.cache.used_pct if d.cache else None, **PCT,
              attrs=lambda d: {"works": d.cache.total, "ready": d.cache.ready, "downloading": d.cache.downloading,
                               "failed": d.cache.failed} if d.cache else {}),
    Ff1Sensor(key="works_needing_attention", state_class=SensorStateClass.MEASUREMENT,
              value=lambda d: len(d.health.works) if d.health else None,
              attrs=lambda d: {"broken": d.health.broken, "of": d.health.total,
                               "works": [f"{w.title or w.index + 1}: {w.state}" + (f" ({w.why})" if w.why else "")
                                         for w in d.health.works[:20]]} if d.health else {}),
    Ff1Sensor(key="outages_24h", state_class=SensorStateClass.MEASUREMENT, entity_category=DIAG,
              value=lambda d: (d.device.outage.count_24h if d.device.outage else 0) if d.device else None),
    Ff1Sensor(key="last_outage", device_class=SensorDeviceClass.TIMESTAMP, entity_category=DIAG,
              value=lambda d: d.device.outage.end if d.device and d.device.outage else None,
              attrs=lambda d: {"kind": d.device.outage.kind} if d.device and d.device.outage else {}),
    Ff1Sensor(key="last_boot", device_class=SensorDeviceClass.TIMESTAMP, entity_category=DIAG, value=_boot),
    Ff1Sensor(key="next_sleep_change", device_class=SensorDeviceClass.TIMESTAMP,
              value=lambda d: d.device.sleep.next_change if d.device and d.device.sleep else None),
    Ff1Sensor(key="orientation", entity_category=DIAG, value=lambda d: (d.device.rotation or None) if d.device else None),
)


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    add(Ff1SensorEntity(entry.runtime_data, s) for s in SENSORS)


class Ff1SensorEntity(Ff1Entity, SensorEntity):
    entity_description: Ff1Sensor

    def __init__(self, coordinator, description: Ff1Sensor) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs(self.data) if self.entity_description.attrs else None
