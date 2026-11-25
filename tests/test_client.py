"""The client against the made-up FF1: what it reads, what it sends, and what it never sends."""

import asyncio
import sys
import unittest
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from fake_ff1 import DEVICE_ID, LAN_OPEN, Ff1  # noqa: E402
from ff1_lan import (CONTROL, READ, Busy, Ff1Client, LanClosed, Locked, NotAllowed, Rejected,  # noqa: E402
                     Unreachable, parse_prometheus)
from ff1_lan.models import Device, Metrics, Panel, Player, Status  # noqa: E402

OWNER_ONLY = ["sshAccess", "factoryReset", "startWifiSetup", "updateToLatestVersion", "shutdown", "setDeviceName",
              "showPairingQRCode", "setContentPolicy", "setSeriesPlayback", "analyticsToggle", "betaFeaturesToggle",
              "sendKeyboardEvent", "tapGesture", "dragGesture", "cursorUpdate", "disconnect", "approveController",
              "revokeController", "listControllers", "registerController", "releaseOwnership", "setLanControl",
              "startMintPairingSession", "mintPairingApprovalDecision", "ff1Envelope", "connect", "uploadLogs",
              "clearPlaylistCache", "anythingNew"]


class Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ff1 = Ff1()
        self.server = TestServer(self.ff1.app(), host="127.0.0.1")
        await self.server.start_server()
        self.session = aiohttp.ClientSession()
        self.client = Ff1Client(self.session, "127.0.0.1", self.server.port, timeout=3)

    async def asyncTearDown(self):
        await self.ff1.close()
        await self.session.close()
        await self.server.close()


class Reads(Case):
    async def test_status(self):
        s = await self.client.status()
        self.assertEqual((s.device_id, s.version, s.claimed, s.control, s.lan_open, s.relay_connected, s.internet),
                         (DEVICE_ID, "3.0.1", True, "enrolled", True, True, True))

    async def test_device_metrics_player_panel_cache(self):
        d = await self.client.device()
        self.assertEqual((d.name, d.volume, d.muted, d.update_available, d.link), ("Hall", 30, False, False, "wifi"))
        self.assertEqual((d.sleep.enabled, d.sleep.sleep_time, d.sleep.sleeping), (True, "22:00", False))
        self.assertEqual((d.outage.kind, d.outage.count_24h, d.outage.start.microsecond), ("wan-down", 2, 123456))
        m = await self.client.metrics()
        self.assertEqual((m.cpu_temp, m.memory_used_pct, m.disk_used_pct, m.screen), (55.5, 25.0, 60.0, "1920x1080@60"))
        p = await self.client.player()
        self.assertEqual((p.now.title, p.now.artist, p.now.thumbnail, len(p.works), p.render, p.sleeping),
                         ("Study #1", "Ada Example", "https://img.example/1.jpg", 3, "ready", False))
        panel = await self.client.panel()
        self.assertEqual((panel.brightness, panel.muted, panel.errors), (40, None, ("power",)))
        c = await self.client.cache()
        self.assertEqual((c.used_pct, c.ready, c.total), (50.0, 9, 10))
        self.assertEqual((await self.client.prometheus())["net_wifi_signal_percent"], 74)

    async def test_media_health_asks_only_for_what_needs_attention(self):
        self.ff1.broken = [{"index": 1, "title": "Study #2", "state": "broken", "category": "source_missing", "skipped": True}]
        h = await self.client.media_health()
        self.assertEqual((h.total, h.broken, h.works[0].why, h.works[0].skipped), (3, 1, "source_missing", True))
        self.assertEqual(self.ff1.calls[-1], ("getMediaHealth", {"filter": "attention"}))

    async def test_recently_played(self):
        self.assertEqual([r["recordId"] for r in await self.client.recently_played()], ["rp-1"])


class Controls(Case):
    async def test_volume_sleep_wake_schedule(self):
        await self.client.set_volume(55)
        self.assertEqual(self.ff1.volume, 55)
        self.assertTrue((await self.client.sleep()).sleeping)
        self.assertFalse((await self.client.wake()).sleeping)
        self.assertFalse((await self.client.set_sleep_schedule(False)).enabled)
        await self.client.set_sleep_schedule(True, "23:5", "6:30", ["mon", "tue"])
        self.assertEqual(self.ff1.calls[-1][1], {"enabled": True, "sleepTime": "23:05", "wakeTime": "06:30", "days": ["mon", "tue"]})

    async def test_shuffle_loop_time_per_work_and_framing(self):
        await self.client.set_shuffle(True)
        await self.client.set_loop("one")
        await self.client.set_work_seconds(600)
        await self.client.set_framing("fill")
        self.assertEqual(self.ff1.calls[-4:], [("setShuffle", {"enabled": True}), ("setLoop", {"mode": "one"}),
                                               ("updateDefaultDuration", {"durationSeconds": 600}),
                                               ("updateArtFraming", {"frameConfig": 1})])
        p = await self.client.player()
        self.assertEqual((p.shuffle, p.loop, p.work_seconds, p.framing), (True, "one", 600, "fill"))
        await self.client.set_work_seconds(None)                 # back to each playlist's own timing
        await self.client.set_framing("fit")
        p = await self.client.player()
        self.assertEqual((p.work_seconds, p.framing), (None, "fit"))

    async def test_step_wraps_round_the_playlist(self):
        self.assertEqual(await self.client.step(-1), 2)
        self.assertEqual(await self.client.step(+1), 0)

    async def test_panel_and_playlist(self):
        await self.client.panel_set("brightness", 80)
        self.assertEqual(self.ff1.panel["brightness"], 80)
        await self.client.show_playlist_url("https://feed.example/playlists/1")
        await self.client.play_recent("rp-1")
        await self.client.reboot()
        self.assertEqual(self.ff1.rebooted, 1)

    async def test_a_built_playlist_is_checked_before_it_is_sent(self):
        doc = {"dpVersion": "1.1.0", "title": "t", "items": [{"source": "https://art.example/1", "duration": 60}]}
        await self.client.show_playlist({"dp1_call": doc})
        self.assertEqual(self.ff1.calls[-1], ("displayPlaylist", {"dp1_call": doc}))
        await self.client.show_playlist({"playlistUrl": "https://feed.example/playlists/1"})
        sent = len(self.ff1.calls)
        for bad in ({}, {"dp1_call": doc, "playlistUrl": "https://x.example/p"}, {"dp1_call": {"items": []}},
                    {"dp1_call": {"items": [{"source": "http://plain.example/1"}]}},
                    {"dp1_call": {"items": [{"source": "https://a.example/1"}] * 101}},
                    {"dp1_call": {"items": [{"source": "https://a.example/1"}], "dynamicQuery": {"endpoint": "https://x"}}},
                    {"dp1_call": {"items": [{"title": "no source"}]}}, {"playlistUrl": "http://plain.example/p"},
                    {"dp1_call": "x"}, {"other": 1}, None):
            with self.assertRaises(NotAllowed, msg=bad):
                await self.client.show_playlist(bad)
        self.assertEqual(len(self.ff1.calls), sent)

    async def test_arguments_out_of_range_never_reach_the_device(self):
        for call in (lambda: self.client.set_volume(101), lambda: self.client.set_volume(True),
                     lambda: self.client.panel_set("brightness", 101), lambda: self.client.panel_set("input", 1),
                     lambda: self.client.panel_set("power", "explode"), lambda: self.client.move_to(-1),
                     lambda: self.client.set_sleep_schedule(True, "25:00"),
                     lambda: self.client.set_sleep_schedule(True, days=[]),
                     lambda: self.client.show_playlist_url("http://plain.example/p"),
                     lambda: self.client.show_playlist_url("file:///etc/passwd"),
                     lambda: self.client.play_recent(""),
                     lambda: self.client.set_shuffle("yes"), lambda: self.client.set_loop("all"),
                     lambda: self.client.set_work_seconds(5), lambda: self.client.set_work_seconds(86401),
                     lambda: self.client.set_work_seconds(60.5), lambda: self.client.set_framing("stretch")):
            with self.assertRaises(NotAllowed):
                await call()
        self.assertEqual(self.ff1.calls, [])


class Refusals(Case):
    async def test_the_client_sends_only_its_own_list(self):
        """Owner-only commands, and LAN-open ones this client has no use for, never leave it."""
        for cmd in OWNER_ONLY:
            with self.assertRaises(NotAllowed, msg=cmd):
                await self.client.cast(cmd, {})
        self.assertEqual(self.ff1.calls, [])

    def test_its_list_is_inside_the_device_lan_open_set(self):
        self.assertLessEqual(READ | CONTROL, LAN_OPEN)
        self.assertFalse(READ & CONTROL)
        for never in ("shutdown", "factoryReset", "sshAccess", "updateToLatestVersion", "startWifiSetup", "ff1Envelope"):
            self.assertNotIn(never, READ | CONTROL)

    async def test_lan_closed_by_the_owner(self):
        self.ff1.lan = "closed"
        self.assertIs((await self.client.status()).lan_open, False)       # status still answers
        with self.assertRaises(LanClosed):
            await self.client.device()
        with self.assertRaises(LanClosed):
            await self.client.set_volume(10)

    async def test_locked_busy_rejected_unreachable(self):
        self.ff1.state = "locked"
        self.assertIs((await self.client.status()).lan_open, False)
        with self.assertRaises(Locked):
            await self.client.device()
        self.ff1.state, self.ff1.busy = "enrolled", True
        with self.assertRaises(Busy):
            await self.client.sleep()
        self.ff1.busy = False
        with self.assertRaises(Rejected):
            await self.client.show_playlist_url("https://dead.example/p")
        with self.assertRaises(Rejected):
            await self.client.play_recent("rp-unknown")
        with self.assertRaises(Unreachable):
            await Ff1Client(self.session, "127.0.0.1", 9, timeout=1).status()


class Notifications(Case):
    async def test_plaintext_status_arrives_and_sealed_copies_are_skipped(self):
        got = []

        async def read():
            async for kind, message in self.client.notifications():
                got.append((kind, message))
                if len(got) == 2:
                    return

        task = asyncio.create_task(read())
        for _ in range(100):
            if self.ff1.sockets:
                break
            await asyncio.sleep(0.01)
        await self.client.set_volume(64)
        await asyncio.wait_for(task, 3)
        self.assertEqual([k for k, _ in got], ["player_status", "device_status"])
        self.assertEqual(Device.parse(got[1][1]).volume, 64)
        self.assertIsNone(Player.parse(got[0][1]).sleeping)

    async def test_a_locked_device_gives_no_socket(self):
        self.ff1.state = "locked"
        with self.assertRaises(Locked):
            async for _ in self.client.notifications():
                self.fail("no message expected")


class Parsing(unittest.TestCase):
    def test_missing_and_odd_fields_never_fail(self):
        for cls in (Device, Metrics, Player, Panel):
            for junk in ({}, None, [], {"volume": "loud", "items": "x", "cpu": 5, "sleepSchedule": 3, "lastOutage": []}):
                cls.parse(junk)
        self.assertIsNone(Player.parse({"index": 9, "items": []}).now)
        self.assertIsNone(Device.parse({"installedVersion": "1"}).update_available)
        self.assertTrue(Device.parse({"installedVersion": "1", "latestVersion": "2"}).update_available)

    def test_status_of_each_control_state(self):
        base = {"device_id": "FF1-X"}
        self.assertIs(Status.parse(base).lan_open, True)                              # before secure control
        self.assertIs(Status.parse({**base, "control": {"state": "legacy"}}).lan_open, True)
        self.assertIs(Status.parse({**base, "control": {"state": "enrolled", "lan": "open"}}).lan_open, True)
        self.assertIs(Status.parse({**base, "control": {"state": "enrolled"}}).lan_open, False)   # absent = closed
        self.assertIs(Status.parse({**base, "control": {"state": "locked"}}).lan_open, False)
        self.assertIsNone(Status.parse({**base, "control": {"state": "new-state"}}).lan_open)

    def test_a_thumbnail_is_https_or_nothing(self):
        item = {"inlineManifest": {"metadata": {"thumbnails": {"small": {"uri": "http://plain.example/a.jpg"}}}}}
        self.assertEqual(Player.parse({"index": 0, "items": [item]}).now.thumbnail, "")

    def test_prometheus(self):
        self.assertEqual(parse_prometheus("# c\na 1\nb{x=\"y z\"} 2.5\nbroken\n"), {"a": 1.0, 'b{x="y z"}': 2.5})


if __name__ == "__main__":
    unittest.main()
