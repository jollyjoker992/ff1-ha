"""The FF1 as a media player: what is on screen, volume, sleep and wake, the next and previous work.

There is no pause on an FF1 (the player has none). "Off" is the device's sleep mode: a dark screen, the
computer still on and answering; "on" wakes it. Neither is a power switch.

Shuffle and repeat are the media player's own: repeat off / all / one is the FF1's loop none / playlist / one
(a new playlist starts again at "all").

Services of this integration, on the same entity, for what a media player has no word for:

    ff1.show_playlist       request: {"playlistUrl": https…} or {"dp1_call": <DP-1 playlist>} (what ff1_playlist builds)
    ff1.rotate              clockwise: true | false (a quarter turn)
    ff1.set_sleep_schedule  enabled, and optionally sleep_time, wake_time ("HH:MM")
    ff1.set_work_duration   seconds: how long each work stays on screen (10 to 86400), for every playlist; 0 or
                            none: each playlist's own timing
    ff1.set_framing         framing: fit (the whole work, with borders) | fill (cropped to fill the screen)

Each is checked by the SDK before it is sent (`Ff1Client.show_playlist`, `set_sleep_schedule`, ...)."""

from __future__ import annotations

from homeassistant.components.media_player import (MediaPlayerDeviceClass, MediaPlayerEntity,
                                                   MediaPlayerEntityFeature, MediaPlayerState, MediaType, RepeatMode)
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import Ff1ConfigEntry
from .entity import Ff1Entity

REPEAT = {RepeatMode.OFF: "none", RepeatMode.ALL: "playlist", RepeatMode.ONE: "one"}   # HA's word: the FF1's


async def async_setup_entry(hass: HomeAssistant, entry: Ff1ConfigEntry, add: AddEntitiesCallback) -> None:
    add([Ff1Player(entry.runtime_data, "player")])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("show_playlist", {vol.Required("request"): dict}, "async_show_playlist")
    platform.async_register_entity_service("rotate", {vol.Optional("clockwise", default=True): cv.boolean}, "async_rotate")
    platform.async_register_entity_service(
        "set_sleep_schedule", {vol.Required("enabled"): cv.boolean, vol.Optional("sleep_time"): cv.string,
                               vol.Optional("wake_time"): cv.string}, "async_set_sleep_schedule")
    platform.async_register_entity_service(
        "set_work_duration", {vol.Optional("seconds"): vol.Any(None, vol.Coerce(int))}, "async_set_work_duration")
    platform.async_register_entity_service("set_framing", {vol.Required("framing"): cv.string}, "async_set_framing")


class Ff1Player(Ff1Entity, MediaPlayerEntity):
    platform_name = "media_player"
    needs_control = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_media_content_type = MediaType.IMAGE
    _attr_media_image_remotely_accessible = True
    _attr_supported_features = (MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE
                                | MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
                                | MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
                                | MediaPlayerEntityFeature.SHUFFLE_SET | MediaPlayerEntityFeature.REPEAT_SET)

    @property
    def state(self) -> MediaPlayerState | None:
        if self.data.sleeping:
            return MediaPlayerState.OFF
        return MediaPlayerState.PLAYING if self.data.player and self.data.player.now else MediaPlayerState.IDLE

    @property
    def _now(self):
        return self.data.player.now if self.data.player and not self.data.sleeping else None

    @property
    def media_title(self) -> str | None:
        return self._now.title or None if self._now else None

    @property
    def media_artist(self) -> str | None:
        return self._now.artist or None if self._now else None

    @property
    def media_image_url(self) -> str | None:
        return self._now.thumbnail or None if self._now else None

    @property
    def media_content_id(self) -> str | None:
        return self._now.id or None if self._now else None

    @property
    def volume_level(self) -> float | None:
        v = self.data.device.volume if self.data.device else None
        return v / 100 if v is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        return self.data.device.muted if self.data.device else None

    @property
    def shuffle(self) -> bool | None:
        return self.data.player.shuffle if self.data.player else None

    @property
    def repeat(self) -> RepeatMode | None:
        loop = self.data.player.loop if self.data.player else ""
        return next((mode for mode, word in REPEAT.items() if word == loop), None)

    @property
    def extra_state_attributes(self) -> dict:
        p = self.data.player
        return {"position": p.index + 1 if p and p.index is not None else None, "works": len(p.works) if p else None,
                "render": p.render if p else None, "framing": (p.framing or None) if p else None,
                "seconds_per_work": p.work_seconds if p else None}

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self.coordinator.act(self.coordinator.client.set_shuffle(bool(shuffle)))

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self.coordinator.act(self.coordinator.client.set_loop(REPEAT.get(RepeatMode(repeat), "")))

    async def async_set_work_duration(self, seconds: int | None = None) -> None:
        await self.coordinator.act(self.coordinator.client.set_work_seconds(seconds or None))

    async def async_set_framing(self, framing: str) -> None:
        await self.coordinator.act(self.coordinator.client.set_framing(framing))

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.act(self.coordinator.client.set_volume(round(volume * 100)))

    async def async_mute_volume(self, mute: bool) -> None:
        if self.is_volume_muted is not mute:            # the device only toggles
            await self.coordinator.act(self.coordinator.client.toggle_mute())

    async def async_turn_off(self) -> None:
        await self.coordinator.act(self.coordinator.client.sleep())

    async def async_turn_on(self) -> None:
        await self.coordinator.act(self.coordinator.client.wake())

    async def async_media_next_track(self) -> None:
        await self.coordinator.act(self.coordinator.client.step(+1))

    async def async_media_previous_track(self) -> None:
        await self.coordinator.act(self.coordinator.client.step(-1))

    async def async_show_playlist(self, request: dict) -> None:
        await self.coordinator.act(self.coordinator.client.show_playlist(request))

    async def async_rotate(self, clockwise: bool = True) -> None:
        await self.coordinator.act(self.coordinator.client.rotate(clockwise))

    async def async_set_sleep_schedule(self, enabled: bool, sleep_time: str | None = None,
                                       wake_time: str | None = None) -> None:
        await self.coordinator.act(self.coordinator.client.set_sleep_schedule(enabled, sleep_time, wake_time))
