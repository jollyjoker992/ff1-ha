"""What the FF1 reports, as plain values. Every parser takes what the device sent and never fails on a field
that is missing or new: firmware adds fields, and a part the device could not read is simply absent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

RENDER = {0: "pending", 1: "loading", 2: "ready", 3: "failed"}
KIB = 1024


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))   # the device writes nanoseconds
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Status:
    """`GET /api/v2/status`."""
    device_id: str
    version: str = ""
    branch: str = ""
    claimed: bool = False
    internet: bool | None = None
    connectivity: str = ""
    setup_state: str = ""
    relay_connected: bool | None = None       # the relay that carries commands from outside the home
    control: str = ""                         # "", legacy, enrolled, locked ("" = firmware before secure control)
    lan_open: bool | None = None              # the owner's LAN switch; None when the device does not say

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Status:
        control = _dict(d.get("control"))
        state = _str(control.get("state"))
        relay = d.get("relay_v2_connected") if "relay_v2_connected" in d else d.get("relayer_connected")
        lan = None
        if state == "enrolled":
            lan = control.get("lan") == "open"
        elif state in ("", "legacy"):
            lan = True
        elif state == "locked":
            lan = False
        return cls(device_id=_str(d.get("device_id")), version=_str(d.get("version")), branch=_str(d.get("branch")),
                   claimed=bool(d.get("claimed")), internet=d.get("internet") if isinstance(d.get("internet"), bool) else None,
                   connectivity=_str(d.get("connectivity")), setup_state=_str(d.get("setup_state")),
                   relay_connected=relay if isinstance(relay, bool) else None, control=state, lan_open=lan)


@dataclass(frozen=True)
class SleepSchedule:
    enabled: bool = False
    sleep_time: str = ""
    wake_time: str = ""
    days: tuple[str, ...] = ()                # empty: every day
    sleeping: bool = False
    next_change: datetime | None = None

    @classmethod
    def parse(cls, d: Any) -> SleepSchedule | None:
        if not isinstance(d, dict):
            return None
        return cls(enabled=bool(d.get("enabled")), sleep_time=_str(d.get("sleepTime")), wake_time=_str(d.get("wakeTime")),
                   days=tuple(x for x in d.get("days") or [] if isinstance(x, str)),
                   sleeping=d.get("currentState") == "sleeping", next_change=_time(d.get("nextTransitionAt")))


@dataclass(frozen=True)
class Outage:
    start: datetime | None
    end: datetime | None
    kind: str
    count_24h: int


@dataclass(frozen=True)
class Device:
    """`getDeviceStatus` and the `device_status` notification."""
    name: str = ""
    installed: str = ""
    latest: str = ""
    volume: int | None = None
    muted: bool | None = None
    rotation: str = ""
    wifi: str = ""
    link: str = ""                            # wifi, ethernet, none, unknown
    internet: bool | None = None
    sleep: SleepSchedule | None = None
    outage: Outage | None = None

    @property
    def update_available(self) -> bool | None:
        return self.installed != self.latest if self.installed and self.latest else None

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Device:
        d = _dict(d)
        net, out = _dict(d.get("network")), d.get("lastOutage")
        outage = None
        if isinstance(out, dict):
            outage = Outage(_time(out.get("start")), _time(out.get("end")), _str(out.get("class")),
                            _int(out.get("count24h")) or 0)
        return cls(name=_str(d.get("deviceName")), installed=_str(d.get("installedVersion")),
                   latest=_str(d.get("latestVersion")), volume=_int(d.get("volume")),
                   muted=d.get("isMuted") if isinstance(d.get("isMuted"), bool) else None,
                   rotation=_str(d.get("screenRotation")), wifi=_str(d.get("connectedWifi")), link=_str(net.get("link")),
                   internet=net.get("internet") if isinstance(net.get("internet"), bool) else None,
                   sleep=SleepSchedule.parse(d.get("sleepSchedule")), outage=outage)


@dataclass(frozen=True)
class Metrics:
    """`deviceMetrics`: the device's own computer. Disk is its root filesystem, not the artwork cache."""
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    gpu_busy: float | None = None
    memory_used_pct: float | None = None
    disk_used_pct: float | None = None
    disk_free_gb: float | None = None
    uptime_s: float | None = None
    screen: str = ""
    at: datetime | None = None

    @classmethod
    def parse(cls, d: Any) -> Metrics:
        d = _dict(d)
        cpu, gpu, mem, disk, screen = (_dict(d.get(k)) for k in ("cpu", "gpu", "memory", "disk", "screen"))
        mem_max, mem_used = _num(mem.get("max_capacity")), _num(mem.get("used_capacity"))
        total, free = _num(disk.get("total_capacity")), _num(disk.get("available_capacity"))     # KiB
        w, h, hz = _int(screen.get("width")), _int(screen.get("height")), _num(screen.get("refresh_rate"))
        return cls(cpu_temp=_num(cpu.get("current_temperature")), gpu_temp=_num(gpu.get("current_temperature")),
                   gpu_busy=_num(gpu.get("gpu_busy")),
                   memory_used_pct=round(100 * mem_used / mem_max, 1) if mem_max and mem_used is not None else None,
                   disk_used_pct=round(100 * (total - free) / total, 1) if total and free is not None else None,
                   disk_free_gb=round(free * KIB / 1e9, 1) if total and free is not None else None,
                   uptime_s=_num(d.get("uptime")), screen=f"{w}x{h}@{hz:g}" if w and h and hz else "",
                   at=_time(d.get("timestamp")))


@dataclass(frozen=True)
class Work:
    index: int
    id: str = ""
    title: str = ""
    artist: str = ""
    thumbnail: str = ""
    duration_s: int | None = None

    @classmethod
    def parse(cls, index: int, d: Any) -> Work:
        d = _dict(d)
        meta = _dict(_dict(d.get("inlineManifest")).get("metadata"))
        artists = [_str(a.get("name")) for a in meta.get("artists") or [] if isinstance(a, dict)]
        thumbs = _dict(meta.get("thumbnails"))
        thumb = next((_str(_dict(thumbs.get(k)).get("uri")) for k in ("medium", "small", "large", "default")
                      if _dict(thumbs.get(k)).get("uri")), "")
        return cls(index=index, id=_str(d.get("id")), title=_str(d.get("title")) or _str(meta.get("title")),
                   artist=", ".join(a for a in artists if a), thumbnail=thumb if thumb.startswith("https://") else "",
                   duration_s=_int(d.get("duration")))


@dataclass(frozen=True)
class Player:
    """`checkStatus` and the `player_status` notification: what the player has on screen."""
    index: int | None = None
    works: tuple[Work, ...] = ()
    render: str = ""                          # pending, loading, ready, failed
    paused: bool | None = None
    sleeping: bool | None = None              # only `checkStatus` says; the notification does not
    shuffle: bool | None = None
    loop: str = ""
    context: str = ""
    orientation: str = ""
    overlay: str = ""                         # who paints over the art (setup, pairing), "" when nobody

    @property
    def now(self) -> Work | None:
        return self.works[self.index] if self.index is not None and 0 <= self.index < len(self.works) else None

    @classmethod
    def parse(cls, d: Any) -> Player:
        d = _dict(d)
        items = d.get("items") if isinstance(d.get("items"), list) else []
        overlay = _dict(d.get("overlay"))
        return cls(index=_int(d.get("index")), works=tuple(Work.parse(i, x) for i, x in enumerate(items)),
                   render=RENDER.get(_int(d.get("renderStatus")), ""),
                   paused=d.get("isPaused") if isinstance(d.get("isPaused"), bool) else None,
                   sleeping=d.get("sleepMode") if isinstance(d.get("sleepMode"), bool) else None,
                   shuffle=d.get("shuffle") if isinstance(d.get("shuffle"), bool) else None,
                   loop=_str(d.get("loopMode")), context=_str(d.get("contentContext")),
                   orientation=_str(_dict(d.get("deviceSettings")).get("orientation")),
                   overlay=_str(overlay.get("owner")))


@dataclass(frozen=True)
class Panel:
    """`ddcPanelStatus`: the screen itself, over DDC. A panel that does not answer a value leaves it None."""
    monitor: str = ""
    brightness: int | None = None
    contrast: int | None = None
    volume: int | None = None
    muted: bool | None = None
    power: str = ""
    errors: tuple[str, ...] = ()

    @classmethod
    def parse(cls, d: Any) -> Panel:
        d = _dict(d)
        mute = d.get("mute")
        return cls(monitor=_str(d.get("monitor")), brightness=_int(d.get("brightness")), contrast=_int(d.get("contrast")),
                   volume=_int(d.get("volume")), muted=(mute == "on") if mute in ("on", "off") else None,
                   power=_str(d.get("power")), errors=tuple(sorted(_dict(d.get("errors")))))


@dataclass(frozen=True)
class Cache:
    """`getOfflineCacheStatus`: the works kept on the device so they play without the internet. Bytes."""
    used: int | None = None
    limit: int | None = None
    total: int | None = None
    ready: int | None = None
    downloading: int | None = None
    failed: int | None = None

    @property
    def used_pct(self) -> float | None:
        return round(100 * self.used / self.limit, 1) if self.limit and self.used is not None else None

    @classmethod
    def parse(cls, d: Any) -> Cache:
        d = _dict(d)
        t = _dict(d.get("totals"))
        return cls(used=_int(d.get("diskUsed")), limit=_int(d.get("diskLimit")), total=_int(t.get("total")),
                   ready=_int(t.get("ready")), downloading=_int(t.get("downloading")), failed=_int(t.get("failed")))


@dataclass(frozen=True)
class Trouble:
    index: int
    title: str
    state: str                                # broken, suspect, unknown, ok (with an issue)
    why: str
    skipped: bool


@dataclass(frozen=True)
class MediaHealth:
    """`getMediaHealth` with `filter: attention`: the works of the playlist on screen that need a look."""
    total: int = 0
    works: tuple[Trouble, ...] = field(default_factory=tuple)

    @property
    def broken(self) -> int:
        return sum(1 for w in self.works if w.state == "broken")

    @classmethod
    def parse(cls, d: Any) -> MediaHealth:
        d = _dict(d)
        works = tuple(Trouble(index=_int(x.get("index")) or 0, title=_str(x.get("title")), state=_str(x.get("state")),
                              why=_str(x.get("category")) or _str(x.get("issue")) or _str(x.get("reason")),
                              skipped=bool(x.get("skipped")))
                      for x in d.get("items") or [] if isinstance(x, dict))
        return cls(total=_int(d.get("total")) or 0, works=works)


def parse_prometheus(text: str) -> dict[str, float]:
    """`GET /metrics` as {name: value}; a labelled sample is `name{labels}`. Lines that are not samples are skipped."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            continue
    return out
