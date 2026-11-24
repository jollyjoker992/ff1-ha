"""A made-up FF1 on 127.0.0.1: the LAN hub's routes with the device's own rules, so the client and the
integration are tested against refusals as well as answers. Nothing here is a real device's data.

The rules it keeps (ffos-user `docs/ff1-control-v1.md` §7): a command outside the LAN-open set is refused
`403 enrollment_required`; with the owner's switch closed every command is; a locked device answers `401`;
status and /metrics answer in every state; the socket carries plaintext status only while the switch is open."""

from __future__ import annotations

import asyncio
import json

from aiohttp import WSMsgType, web

DEVICE_ID = "FF1-TEST0001"
# the device's controller tier (control/tiers.go): what the LAN may send in plaintext
LAN_OPEN = {"connect", "displayPlaylist", "displayDefaultPlaylist", "refreshArtwork", "getRecentlyPlayed",
            "playRecentlyPlayed", "downloadPlaylistItem", "downloadPlaylist", "clearPlaylistItemCache",
            "clearPlaylistCache", "getOfflineCacheStatus", "getMediaHealth", "getDeviceStatus", "deviceMetrics",
            "rotate", "setVolume", "toggleMute", "ddcPanelControl", "ddcPanelStatus", "setSleepSchedule",
            "setSleepMode", "sleepNow", "wakeNow", "reboot", "runNetworkDiagnostics", "getContentPolicy",
            "getSeriesPlayback", "uploadLogs", "checkStatus", "moveToArtwork", "updateIndex", "setShuffle", "setLoop",
            "updateArtFraming", "updateDisplaySettings", "updateDefaultDuration", "refreshPlaylist"}


def work(n: int) -> dict:
    return {"id": f"work-{n}", "title": f"Study #{n}", "duration": 300, "source": f"https://art.example/{n}?sig=secret",
            "inlineManifest": {"metadata": {"title": f"Study #{n}", "artists": [{"name": "Ada Example"}],
                                            "thumbnails": {"small": {"uri": f"https://img.example/{n}.jpg"}}}}}


class Ff1:
    def __init__(self) -> None:
        self.state = "enrolled"               # legacy, enrolled, locked
        self.lan = "open"
        self.volume, self.muted, self.sleeping = 30, False, False
        self.schedule = True
        self.index, self.render = 0, 2
        self.works = [work(n) for n in range(1, 4)]
        self.panel = {"brightness": 40, "contrast": 50, "volume": 20, "monitor": "TST:HDMI", "errors": {"power": "VCP reported ERR"}}
        self.broken: list[dict] = []
        self.latest = "3.0.1"
        self.calls: list[tuple[str, dict]] = []
        self.busy = False
        self.sockets: list[web.WebSocketResponse] = []
        self.rebooted = 0

    # ---------- what it reports ----------
    def status(self) -> dict:
        control = {"v": 1, "state": self.state}
        if self.state == "enrolled":
            control["lan"] = self.lan
        return {"device_id": DEVICE_ID, "version": "3.0.1", "branch": "release", "contract": "2", "claimed": True,
                "internet": True, "relayer_connected": False, "relay_v2_connected": True, "setup_state": "online",
                "connectivity": "connected", "topic_id": "", "control": control}

    def device(self) -> dict:
        return {"contract": "2", "deviceName": "Hall", "installedVersion": "3.0.1", "latestVersion": self.latest,
                "volume": self.volume, "isMuted": self.muted, "screenRotation": "portrait", "connectedWifi": "TestNet",
                "network": {"state": "online", "link": "wifi", "internet": True},
                "lastOutage": {"start": "2026-11-24T02:10:00.123456789Z", "end": "2026-11-24T02:14:30Z",
                               "class": "wan-down", "count24h": 2},
                "sleepSchedule": {"enabled": self.schedule, "sleepTime": "22:00", "wakeTime": "06:00",
                                  "currentState": "sleeping" if self.sleeping else "awake",
                                  "nextTransitionAt": "2026-11-24T22:00:00+07:00"}}

    def player(self, notification: bool = False) -> dict:
        d = {"ok": True, "castCommand": "displayPlaylist", "index": self.index, "renderStatus": self.render,
             "isPaused": False, "items": self.works, "loopMode": "playlist", "shuffle": False,
             "contentContext": "curated", "deviceSettings": {"orientation": "landscape"}}
        if not notification:
            d["sleepMode"] = self.sleeping
        return d

    async def push(self, kind: str, message: dict) -> None:
        for ws in list(self.sockets):
            await ws.send_json({"type": "notification", "notification_type": kind, "persist_record_count": 1,
                                "message": message})

    # ---------- the routes ----------
    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/v2/status", self._status)
        app.router.add_post("/api/cast", self._cast)
        app.router.add_get("/api/notification", self._ws)
        app.router.add_get("/metrics", self._metrics)
        app.router.add_get("/api/screenshot", self._refused)
        return app

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(self.status())

    async def _refused(self, request: web.Request) -> web.Response:
        return web.json_response({"error": {"code": "enrollment_required"}}, status=403)

    async def _metrics(self, request: web.Request) -> web.Response:
        return web.Response(text="# HELP x\nnet_wifi_signal_percent 74\nplayback_start_total 120\n"
                                 "playback_start_failures_total 1\ncontrol_state{state=\"enrolled\"} 1\n")

    async def _cast(self, request: web.Request) -> web.Response:
        body = await request.json()
        cmd, req = body.get("command"), body.get("request") or {}
        self.calls.append((cmd, req))
        if self.busy:
            return web.Response(status=429, text="rate limited")
        if self.state == "locked":
            return web.json_response({"error": {"code": "locked"}}, status=401)
        if cmd not in LAN_OPEN or (self.state == "enrolled" and self.lan != "open"):
            return web.json_response({"error": {"code": "enrollment_required"}}, status=403)
        return await getattr(self, "_do_" + cmd, self._ok)(req)

    async def _ok(self, req: dict) -> web.Response:
        return web.json_response({"ok": True})

    async def _do_getDeviceStatus(self, req):
        return web.json_response(self.device())

    async def _do_deviceMetrics(self, req):
        return web.json_response({"cpu": {"current_temperature": 55.5}, "gpu": {"current_temperature": 60, "gpu_busy": 40},
                                  "memory": {"max_capacity": 8000, "used_capacity": 2000},
                                  "disk": {"total_capacity": 100_000_000, "available_capacity": 40_000_000},
                                  "screen": {"width": 1920, "height": 1080, "refresh_rate": 60},
                                  "timestamp": "2026-11-24T10:00:30.5Z", "uptime": 3600.0})

    async def _do_checkStatus(self, req):
        return web.json_response({"message": self.player()})

    async def _do_ddcPanelStatus(self, req):
        return web.json_response(self.panel)

    async def _do_ddcPanelControl(self, req):
        if req.get("action") not in ("brightness", "contrast", "volume", "mute", "power"):
            return web.Response(status=500, text="unknown action")
        self.panel[req["action"]] = req["value"]
        return web.json_response({"ok": True})

    async def _do_getOfflineCacheStatus(self, req):
        return web.json_response({"ok": True, "diskUsed": 30_000_000_000, "diskLimit": 60_000_000_000,
                                  "totals": {"total": 10, "ready": 9, "downloading": 1, "failed": 0}, "items": []})

    async def _do_getMediaHealth(self, req):
        return web.json_response({"ok": True, "status": "ok", "total": len(self.works), "items": self.broken})

    async def _do_getRecentlyPlayed(self, req):
        if req:
            return web.json_response({"message": {"ok": False, "status": "error", "error": "invalid request"}})
        return web.json_response({"message": {"ok": True, "records": [
            {"recordId": "rp-1", "title": "Study #1", "artist": "Ada Example", "isActive": True, "playedAtMs": 1}]}})

    async def _do_playRecentlyPlayed(self, req):
        if req.get("recordId") != "rp-1":
            return web.json_response({"message": {"ok": False, "status": "error", "error": "unknown record"}})
        return web.json_response({"message": {"ok": True}})

    async def _do_setVolume(self, req):
        self.volume = req["percent"]
        await self.push("device_status", self.device())
        return web.json_response({"ok": True})

    async def _do_toggleMute(self, req):
        self.muted = not self.muted
        return web.json_response({"ok": True})

    async def _do_sleepNow(self, req):
        self.sleeping = True
        return web.json_response({"ok": True, "sleepSchedule": self.device()["sleepSchedule"]})

    async def _do_wakeNow(self, req):
        self.sleeping = False
        return web.json_response({"ok": True, "sleepSchedule": self.device()["sleepSchedule"]})

    async def _do_setSleepSchedule(self, req):
        self.schedule = bool(req.get("enabled"))
        return web.json_response({"ok": True, "sleepSchedule": self.device()["sleepSchedule"]})

    async def _do_moveToArtwork(self, req):
        self.index = req["index"]
        return web.json_response({"message": {"ok": True}})

    async def _do_rotate(self, req):
        return web.json_response({"orientation": "landscape"})

    async def _do_displayPlaylist(self, req):
        if "dead" in req.get("playlistUrl", ""):
            return web.Response(status=422, text="sourceUnreachable: no playlist item source is loadable")
        return web.json_response({"message": {"ok": True}})

    async def _do_reboot(self, req):
        self.rebooted += 1
        return web.json_response({"ok": True})

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if self.state == "locked":
            return web.json_response({"error": {"code": "locked"}}, status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        # a copy sealed for a paired controller rides the same socket: not ours to read
        await ws.send_json({"type": "notification", "notification_type": "sealed", "persist_record_count": 0,
                            "message": {"v": 1, "ctl": "someone-else", "jwe": "e30.x.y.z.w"}})
        if self.state != "enrolled" or self.lan == "open":
            await self.push("player_status", self.player(notification=True))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.CLOSE:
                    break
        finally:
            self.sockets.remove(ws)
        return ws

    async def close(self) -> None:
        for ws in list(self.sockets):
            await ws.close()
        await asyncio.sleep(0)


def dumps(value: object) -> str:
    return json.dumps(value)
