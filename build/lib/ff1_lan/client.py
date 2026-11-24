"""The FF1's LAN hub (feral-controld, port 1111), as a controller on the device's own network sees it.

What the device serves there (ffos-user `docs/api-design.md`, `docs/ff1-control-v1.md` §7):

- `GET /api/v2/status`: identity, firmware, claim, connectivity, the control block (state, the owner's LAN switch);
- `POST /api/cast` `{"command", "request"}`: the same commands the Feral File app sends. Without a controller's
  keys the device takes only its **LAN-open set**: what the next command can undo (playback, volume, sleep and
  wake, the panel over DDC, rotate, reboot, the status reads). Everything else is the owner's alone and needs a
  signed, encrypted envelope, which this client never sends;
- `GET /api/notification` (WebSocket): `device_status`, `player_status`, `ddc_status`, `offline_cache_status` as
  they change. Copies sealed for paired controllers travel on the same socket and are skipped;
- `GET /metrics`: Prometheus text (playback counters, Wi-Fi signal, relay state).

This client holds no key and no account: it can do exactly what any phone on the same Wi-Fi can. It sends only
the commands listed in `READ` and `CONTROL` (a subset of the LAN-open set), so an owner-only command (a shell,
a factory reset, Wi-Fi setup, an update, shutdown, input injection) cannot leave through it even by mistake;
the device would refuse those anyway. When the owner closes the LAN switch every command is refused
(`LanClosed`) while status and metrics still answer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .models import Cache, Device, MediaHealth, Metrics, Panel, Player, SleepSchedule, Status, parse_prometheus

PORT = 1111

# Reads: change nothing on the device.
READ = frozenset({"getDeviceStatus", "deviceMetrics", "checkStatus", "ddcPanelStatus", "getOfflineCacheStatus",
                  "getMediaHealth", "getRecentlyPlayed", "getContentPolicy", "getSeriesPlayback"})
# Controls: each is undone by the next command or a wait (the device's own rule for its LAN-open set).
CONTROL = frozenset({"setVolume", "toggleMute", "sleepNow", "wakeNow", "setSleepSchedule", "ddcPanelControl",
                     "rotate", "moveToArtwork", "playRecentlyPlayed", "displayDefaultPlaylist", "refreshArtwork",
                     "displayPlaylist", "reboot"})
NOTIFICATIONS = frozenset({"device_status", "player_status", "ddc_status", "offline_cache_status"})
PANEL_ACTIONS = {"brightness": range(0, 101), "contrast": range(0, 101), "volume": range(0, 101),
                 "mute": ("on", "off"), "power": ("on", "standby", "off")}
SLOW = {"runNetworkDiagnostics": 30, "ddcPanelStatus": 20, "ddcPanelControl": 20, "displayPlaylist": 30,
        "playRecentlyPlayed": 30, "displayDefaultPlaylist": 30}


class Ff1Error(Exception):
    """The FF1 did not do what was asked."""


class Unreachable(Ff1Error):
    """No answer: off, asleep at the plug, another address, or not an FF1."""


class NotAllowed(Ff1Error):
    """This client does not send that command (not in READ or CONTROL), or its arguments are out of range."""


class LanClosed(Ff1Error):
    """403 enrollment_required: the owner closed LAN control, or the command is the owner's alone."""


class NotLocal(Ff1Error):
    """403 lan_only: the device does not see the caller as being on its own network."""


class Locked(Ff1Error):
    """401 locked: the device's control state is damaged; it refuses everything until its owner resets it."""


class Busy(Ff1Error):
    """429 / 503: the device shed the command to protect itself. Nothing was applied; try again later."""


class Rejected(Ff1Error):
    """The device understood and said no (bad arguments, a source it cannot load, a blocked work)."""


def _refusal(status: int, text: str) -> Ff1Error:
    code = ""
    try:
        body = json.loads(text)
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code", "") if isinstance(err, dict) else (err if isinstance(err, str) else "")
    except ValueError:
        pass
    what = code or text.strip()[:200] or f"HTTP {status}"
    if status in (429, 503) or code in ("rate_limited", "busy"):
        return Busy(what)
    if code == "lan_only":
        return NotLocal(what)
    if code == "locked" or status == 401:
        return Locked(what)
    if code in ("enrollment_required", "envelope_required", "screen_protected") or status == 403:
        return LanClosed(what)
    return Rejected(what)


class Ff1Client:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int = PORT, timeout: float = 10) -> None:
        self._session = session
        self.base = f"http://{host}:{port}"
        self._timeout = timeout

    # ---------- transport ----------
    async def _get(self, path: str) -> tuple[int, str]:
        try:
            async with self._session.get(self.base + path, timeout=aiohttp.ClientTimeout(total=self._timeout)) as r:
                return r.status, await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise Unreachable(type(e).__name__) from e

    async def cast(self, command: str, request: dict[str, Any] | None = None) -> Any:
        """One command of the LAN-open set. Returns the device's reply (its `message` when it wraps one)."""
        if command not in READ and command not in CONTROL:
            raise NotAllowed(f"{command} is not a command this client sends")
        body = {"command": command, "request": request or {}}
        timeout = aiohttp.ClientTimeout(total=max(self._timeout, SLOW.get(command, 0)))
        try:
            async with self._session.post(self.base + "/api/cast", json=body, timeout=timeout) as r:
                status, text = r.status, await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise Unreachable(type(e).__name__) from e
        if status != 200:
            raise _refusal(status, text)
        try:
            reply = json.loads(text) if text.strip() else {}
        except ValueError as e:
            raise Rejected("the reply is not JSON") from e
        if isinstance(reply, dict) and isinstance(reply.get("message"), dict) and set(reply) <= {"message", "type",
                                                                                                "messageID"}:
            reply = reply["message"]                 # the player's replies come wrapped
        if isinstance(reply, dict) and reply.get("ok") is False:
            err = reply.get("error")
            what = err.get("code") if isinstance(err, dict) else err
            if what in ("enrollment_required", "envelope_required"):
                raise LanClosed(str(what))
            raise (Busy if what == "rate_limited" else Rejected)(str(what or reply.get("status") or "refused"))
        return reply

    # ---------- reads ----------
    async def status(self) -> Status:
        """Who the device is and whether it takes commands from the LAN. Served even with the LAN switch closed."""
        code, text = await self._get("/api/v2/status")
        if code == 404:                              # firmware before the v2 contract
            code, text = await self._get("/api/status")
        if code != 200:
            raise _refusal(code, text)
        try:
            body = json.loads(text)
        except ValueError as e:
            raise Unreachable("not an FF1: the status is not JSON") from e
        if not isinstance(body, dict) or not body.get("device_id"):
            raise Unreachable("not an FF1: the status has no device_id")
        return Status.parse(body)

    async def device(self) -> Device:
        return Device.parse(await self.cast("getDeviceStatus"))

    async def metrics(self) -> Metrics:
        return Metrics.parse(await self.cast("deviceMetrics"))

    async def player(self) -> Player:
        return Player.parse(await self.cast("checkStatus"))

    async def panel(self) -> Panel:
        return Panel.parse(await self.cast("ddcPanelStatus"))

    async def cache(self) -> Cache:
        return Cache.parse(await self.cast("getOfflineCacheStatus"))

    async def media_health(self) -> MediaHealth:
        """The works of the playlist on screen that do not play, or play with trouble."""
        return MediaHealth.parse(await self.cast("getMediaHealth", {"filter": "attention"}))

    async def recently_played(self) -> list[dict[str, Any]]:
        reply = await self.cast("getRecentlyPlayed")
        return [r for r in reply.get("records") or [] if isinstance(r, dict)]

    async def prometheus(self) -> dict[str, float]:
        code, text = await self._get("/metrics")
        if code != 200:
            raise _refusal(code, text)
        return parse_prometheus(text)

    # ---------- controls ----------
    async def set_volume(self, percent: int) -> None:
        await self.cast("setVolume", {"percent": _between("volume", percent, 0, 100)})

    async def toggle_mute(self) -> None:
        await self.cast("toggleMute")

    async def sleep(self) -> SleepSchedule | None:
        """Dark screen until the next wake time of the schedule, or until `wake()`."""
        return SleepSchedule.parse((await self.cast("sleepNow")).get("sleepSchedule"))

    async def wake(self) -> SleepSchedule | None:
        return SleepSchedule.parse((await self.cast("wakeNow")).get("sleepSchedule"))

    async def set_sleep_schedule(self, enabled: bool, sleep_time: str | None = None, wake_time: str | None = None,
                                 days: list[str] | None = None) -> SleepSchedule | None:
        req: dict[str, Any] = {"enabled": bool(enabled)}
        for key, value in (("sleepTime", sleep_time), ("wakeTime", wake_time)):
            if value is not None:
                req[key] = _hhmm(value)
        if days is not None:
            if not days or any(d not in ("sun", "mon", "tue", "wed", "thu", "fri", "sat") for d in days):
                raise NotAllowed("days are sun..sat, at least one")
            req["days"] = list(days)
        return SleepSchedule.parse((await self.cast("setSleepSchedule", req)).get("sleepSchedule"))

    async def panel_set(self, action: str, value: int | str) -> None:
        """Brightness, contrast, the panel's own volume, mute or power, over DDC. Panels differ in what they take."""
        allowed = PANEL_ACTIONS.get(action)
        if allowed is None or isinstance(value, bool) or value not in allowed:
            raise NotAllowed(f"panel {action}={value!r}")
        await self.cast("ddcPanelControl", {"action": action, "value": value})

    async def rotate(self, clockwise: bool = True) -> str:
        return str((await self.cast("rotate", {"clockwise": bool(clockwise)})).get("orientation") or "")

    async def move_to(self, index: int) -> None:
        """Show the work at this place of the playlist on screen."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise NotAllowed("index is a whole number from 0")
        await self.cast("moveToArtwork", {"index": index})

    async def step(self, by: int) -> int:
        """The next (+1) or previous (-1) work of the playlist on screen, wrapping round. Returns the new index."""
        player = await self.player()
        if player.index is None or not player.works:
            raise Rejected("nothing is on display")
        index = (player.index + by) % len(player.works)
        await self.move_to(index)
        return index

    async def play_recent(self, record_id: str) -> None:
        if not isinstance(record_id, str) or not record_id or len(record_id) > 256:
            raise NotAllowed("a record id from recently_played()")
        await self.cast("playRecentlyPlayed", {"recordId": record_id})

    async def show_default(self) -> None:
        await self.cast("displayDefaultPlaylist")

    async def show_playlist_url(self, url: str) -> None:
        """Display a published DP-1 playlist by its URL (https only). The device fetches and verifies it itself."""
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048 or any(c.isspace() for c in url):
            raise NotAllowed("an https URL of a playlist")
        await self.cast("displayPlaylist", {"playlistUrl": url})

    async def show_playlist(self, request: dict[str, Any]) -> None:
        """Display what `ff1_playlist` built: `{"playlistUrl": https…}` or `{"dp1_call": <DP-1 playlist>}`.

        Checked before it is sent: exactly one of the two, a playlist of 1 to 100 items whose sources are all
        https, no `dynamicQuery` (it would make the device call an address of the sender's choosing), and under
        1 MiB. What may be shown is still the device's content policy, set by its owner."""
        if not isinstance(request, dict) or len(request) != 1:
            raise NotAllowed("a request is playlistUrl or dp1_call")
        if "playlistUrl" in request:
            return await self.show_playlist_url(request["playlistUrl"])
        doc = request.get("dp1_call")
        items = doc.get("items") if isinstance(doc, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or "dynamicQuery" in doc:
            raise NotAllowed("a DP-1 playlist of 1 to 100 items, without dynamicQuery")
        for item in items:
            source = item.get("source") if isinstance(item, dict) else None
            if not isinstance(source, str) or not source.startswith("https://") or any(c.isspace() for c in source):
                raise NotAllowed("every item needs an https source")
        if len(json.dumps(doc)) > 1 << 20:
            raise NotAllowed("the playlist is larger than 1 MiB")
        await self.cast("displayPlaylist", {"dp1_call": doc})

    async def refresh(self) -> None:
        """Clear the browser cache and load the work on screen again."""
        await self.cast("refreshArtwork")

    async def reboot(self) -> None:
        await self.cast("reboot")

    # ---------- notifications ----------
    async def notifications(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yields (type, message) as the device pushes them, until the socket closes. Raises Unreachable/LanClosed.

        Only the four plaintext status types. A copy sealed for a paired controller is not ours to open."""
        try:
            ws = await self._session.ws_connect(self.base + "/api/notification", heartbeat=30,
                                                timeout=aiohttp.ClientWSTimeout(ws_close=5), max_msg_size=8 << 20)
        except aiohttp.WSServerHandshakeError as e:
            raise _refusal(e.status, e.message or "") from e
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise Unreachable(type(e).__name__) from e
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                kind = data.get("notification_type") if isinstance(data, dict) else None
                if kind in NOTIFICATIONS and isinstance(data.get("message"), dict):
                    yield kind, data["message"]
        finally:
            await ws.close()


def _between(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise NotAllowed(f"{name} is a whole number {low}..{high}")
    return value


def _hhmm(value: str) -> str:
    parts = value.split(":") if isinstance(value, str) else []
    if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]) and int(parts[0]) < 24 and int(parts[1]) < 60:
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    raise NotAllowed("a time is HH:MM")
