"""One coordinator per FF1: a poll every minute as the safety net, the device's notification socket for changes
as they happen (a new work on screen, volume, sleep), and the slow reads (the panel over DDC, the artwork cache,
the works' health, /metrics) every ten minutes.

With the owner's LAN switch closed the device still answers its status, so the entry stays loaded: the
connectivity sensors keep working and every control is unavailable until the owner opens it again."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ff1_lan import (Cache, Device, Ff1Client, Ff1Error, LanClosed, Locked, MediaHealth, Metrics, Panel, Player,
                     Status, Unreachable)

from .const import (CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN, RECONNECT_MAX, RECONNECT_MIN, SLOW_EVERY,
                    UPDATE_EVERY)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ff1Data:
    status: Status
    device: Device | None = None
    metrics: Metrics | None = None
    player: Player | None = None
    panel: Panel | None = None
    cache: Cache | None = None
    health: MediaHealth | None = None
    wifi_signal: float | None = None
    plays: float | None = None
    play_failures: float | None = None

    @property
    def controllable(self) -> bool:
        """The device takes this network's commands (the owner's LAN switch is open, the device not locked)."""
        return self.status.lan_open is not False and self.device is not None

    @property
    def sleeping(self) -> bool | None:
        if self.player and self.player.sleeping is not None:
            return self.player.sleeping
        return self.device.sleep.sleeping if self.device and self.device.sleep else None


type Ff1ConfigEntry = ConfigEntry[Ff1Coordinator]


class Ff1Coordinator(DataUpdateCoordinator[Ff1Data]):
    def __init__(self, hass: HomeAssistant, entry: Ff1ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_EVERY)
        self.client = Ff1Client(async_get_clientsession(hass), entry.data[CONF_HOST],
                                entry.data.get(CONF_PORT, DEFAULT_PORT))
        self._slow_at: datetime | None = None

    async def _async_update_data(self) -> Ff1Data:
        try:
            status = await self.client.status()
        except Ff1Error as e:
            raise UpdateFailed(f"the FF1 does not answer ({e})") from e
        if status.device_id != self.config_entry.unique_id:
            raise UpdateFailed("another FF1 answers at this address")
        old = self.data
        if status.lan_open is False:
            return Ff1Data(status=status)
        try:
            device = await self.client.device()
            metrics = await self.client.metrics()
            player = await self.client.player()
        except (LanClosed, Locked):               # closed between the status and the commands
            return Ff1Data(status=replace(status, lan_open=False))
        except Ff1Error as e:
            raise UpdateFailed(str(e)) from e
        data = Ff1Data(status=status, device=device, metrics=metrics, player=player,
                       panel=old.panel if old else None, cache=old.cache if old else None,
                       health=old.health if old else None, wifi_signal=old.wifi_signal if old else None,
                       plays=old.plays if old else None, play_failures=old.play_failures if old else None)
        if self._slow_at is None or dt_util.utcnow() - self._slow_at >= SLOW_EVERY:
            data = await self._slow(data)
            self._slow_at = dt_util.utcnow()
        return data

    async def _slow(self, data: Ff1Data) -> Ff1Data:
        """Each of these may fail by itself (a panel without DDC, a busy device) without stopping the rest."""
        for name, read in (("panel", self.client.panel), ("cache", self.client.cache),
                           ("health", self.client.media_health)):
            try:
                data = replace(data, **{name: await read()})
            except Ff1Error as e:
                _LOGGER.debug("reading the %s failed: %s", name, e)
        try:
            prom = await self.client.prometheus()
            data = replace(data, wifi_signal=prom.get("net_wifi_signal_percent"), plays=prom.get("playback_start_total"),
                           play_failures=prom.get("playback_start_failures_total"))
        except Ff1Error as e:
            _LOGGER.debug("reading the metrics failed: %s", e)
        return data

    # ---------- the notification socket ----------
    def listen(self) -> None:
        self.config_entry.async_create_background_task(self.hass, self._listen(), f"{DOMAIN} notifications")

    async def _listen(self) -> None:
        wait = RECONNECT_MIN
        while True:
            try:
                async for kind, message in self.client.notifications():
                    wait = RECONNECT_MIN
                    self._notified(kind, message)
            except (Unreachable, LanClosed, Locked) as e:
                _LOGGER.debug("no notification socket (%s); the poll goes on", e)
            except Ff1Error as e:
                _LOGGER.debug("the notification socket failed: %s", e)
            await asyncio.sleep(wait)
            wait = min(wait * 2, RECONNECT_MAX)

    def _notified(self, kind: str, message: dict) -> None:
        data = self.data
        if data is None or not data.controllable:
            return
        if kind == "device_status":
            data = replace(data, device=Device.parse(message))
        elif kind == "player_status":
            player = Player.parse(message)        # the notification does not say whether the player sleeps
            data = replace(data, player=replace(player, sleeping=None))
        elif kind == "ddc_status":
            data = replace(data, panel=Panel.parse(message))
        else:
            return
        self.async_set_updated_data(data)

    async def act(self, call) -> None:
        """Run one control and read the device again; a refusal reaches the person who asked."""
        try:
            await call
        except Ff1Error as e:
            raise HomeAssistantError(f"The FF1 refused: {type(e).__name__} ({e})") from e
        await self.async_refresh()
