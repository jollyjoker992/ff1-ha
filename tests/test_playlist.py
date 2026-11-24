"""The playlist builder against canned answers of the four sources (made-up works), and the signing contract
against a playlist the public DP-1 feed signed."""

import json
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from ff1_playlist import builder as B  # noqa: E402
from ff1_playlist import signing  # noqa: E402
from ff1_playlist import vendors as V  # noqa: E402
from ff1_playlist.__main__ import main  # noqa: E402

ARTIST = "0x" + "ab" * 20
PROJECT = "0x" + "cd" * 20 + "-7"
FEED_ID = "11111111-2222-3333-4444-555555555555"


class Sources:
    """A stand-in for `vendors.fetch`: answers as each source does, records what was asked, can be down."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down: set[str] = set()
        self.raster_works = 5

    def __call__(self, url, body=None, timeout=30):
        host = urllib.parse.urlsplit(url).hostname
        self.calls.append(url)
        if host in self.down:
            raise V.VendorError(f"{host}: down")
        if url.startswith(V.FEED + "/playlists?"):
            if "channel=artblocks" not in url:
                return {"items": [], "hasMore": False}
            return {"hasMore": False, "items": [
                {"id": FEED_ID, "title": "Drift", "items": [{}] * 8, "signatures": [{"alg": "ed25519"}]},
                {"id": "99999999-2222-3333-4444-555555555555", "title": "Drift unsigned", "items": [{}]}]}
        if url.startswith(V.FEED + "/playlists/"):
            return {"title": "Drift", "items": [{"source": "https://x.example/1"}] * 8, "signatures": [{"alg": "ed25519"}]}
        if host == "data.artblocks.io":
            q, v = body["query"], body["variables"]
            projects = [{"id": PROJECT, "name": "Drift", "artist_name": "Ada Example", "artist_address": ARTIST, "invocations": 300}]
            if "tokens_metadata" in q:
                return {"data": {"projects_metadata": projects, "tokens_metadata": [
                    {"token_id": str(7000000 + i), "invocation": i, "contract_address": PROJECT[:42],
                     "live_view_url": f"https://generator.artblocks.io/1/{PROJECT[:42]}/{7000000 + i}",
                     "preview_asset_url": f"https://media-proxy.artblocks.io/1/{i}.png"} for i in range(v["n"])]}}
            if "artist_address:{_eq" in q.replace(" ", ""):
                return {"data": {"projects_metadata": projects if v["a"] == ARTIST else []}}
            term = v["q"].strip("%").lower()
            return {"data": {"projects_metadata": projects if term in "drift" or term in "ada example" else []}}
        if host == "data.objkt.com":
            q, v = body["query"], body["variables"]
            if "token(" in q:
                return {"data": {"token": [
                    {"token_id": "1", "name": "Tide", "mime": "image/png", "artifact_uri": "ipfs://Qm" + "a" * 44,
                     "display_uri": "ipfs://Qm" + "b" * 44, "fa_contract": "KT1" + "A" * 33, "creators": [{"holder": {"alias": "Bo"}}]},
                    {"token_id": "2", "name": "Script", "mime": "text/html", "artifact_uri": "javascript:alert(1)", "display_uri": ""},
                    {"token_id": "3", "name": "Plain", "mime": "video/mp4", "artifact_uri": "http://plain.example/v.mp4", "display_uri": ""}]}}
            if "contract:{_eq" in q:
                return {"data": {"fa": [{"name": "Tides", "items": 3, "creator": {"alias": "Bo"}}]}}
            term = v["q"].strip("%").lower()
            return {"data": {"fa": [{"contract": "KT1" + "A" * 33, "name": "Tides", "items": 3, "creator": {"alias": "Bo"}}]
                             if term in "tides" else [], "holder": []}}
        if url.startswith(V.RESOLVER + "/work"):
            return {"found": True, "work": {"title": "Drift #3", "artist": "Ada Example", "series": "Drift", "media": "web",
                                            "source": "https://generator.artblocks.io/1/x/7000003", "chain": "ethereum",
                                            "contract": PROJECT[:42], "tokenId": "7000003", "standard": "erc721"}}
        if url.startswith(V.RESOLVER + "/raster/artists"):
            return {"artists": [{"id": "42", "name": "Ada Example"}]}
        if url.startswith(V.RESOLVER + "/raster/artist-works"):
            return {"next": "", "works": [
                {"title": f"Piece {i}", "artists": [{"name": "Ada Example"}], "tokens": {"nodes": [
                    {"chainId": "eip155:1", "contractAddress": "0x" + "ef" * 20, "tokenId": str(i),
                     "media": {"contentUrl": f"https://media.example/{i}.mp4", "contentType": "video"}}]}}
                for i in range(self.raster_works)]}
        raise AssertionError("not a source: " + url)


class Finding(unittest.TestCase):
    def setUp(self):
        self.src = Sources()

    def test_a_signed_feed_playlist_wins_over_the_vendor_collection(self):
        found, failed = B.find("drift", get=self.src)
        self.assertEqual([(c.vendor, c.kind, c.title) for c in found], [("feed", "playlist", "Drift"), ("artblocks", "collection", "Drift")])
        self.assertEqual(failed, [])

    def test_an_unsigned_feed_playlist_and_other_channels_are_never_offered(self):
        found, _ = B.find("drift unsigned", get=self.src)
        self.assertEqual(found, [])
        asked = [u for u in self.src.calls if "/playlists?" in u]
        self.assertEqual({urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["channel"][0] for u in asked}, set(V.FEED_CHANNELS))

    def test_an_artist_name_gives_the_artist_before_one_collection_by_them(self):
        found, _ = B.find("ada example", get=self.src)
        self.assertEqual((found[0].vendor, found[0].kind, found[0].id), ("artblocks", "artist", ARTIST))

    def test_a_source_that_is_down_is_named_and_the_rest_still_answer(self):
        self.src.down = {"feed.feralfile.com", "data.objkt.com"}
        found, failed = B.find("drift", get=self.src)
        self.assertEqual([c.vendor for c in found], ["artblocks"])
        self.assertEqual(sorted(f.split(":")[0] for f in failed), ["feed", "objkt"])

    def test_a_link_to_one_work_finds_its_whole_series(self):
        found, _ = B.find("https://www.artblocks.io/collections/x/projects/y/7", get=self.src)
        self.assertEqual([(c.vendor, c.kind) for c in found], [("feed", "playlist"), ("artblocks", "collection"), ("resolver", "work")])

    def test_what_is_asked_is_bounded(self):
        for bad in ("", "a", "x" * 301):
            with self.assertRaises(ValueError):
                B.find(bad, get=self.src)
        with self.assertRaises(ValueError):
            B.find("drift", vendors=["raster"], get=self.src)


class Building(unittest.TestCase):
    def setUp(self):
        self.src = Sources()
        self.key = signing.Key.generate()

    def test_a_feed_playlist_is_played_by_its_url_untouched(self):
        p = B.build(B.find("drift", get=self.src)[0][0], key=self.key, get=self.src)
        self.assertEqual(p.request(), {"playlistUrl": f"{V.FEED}/playlists/{FEED_ID}"})
        self.assertEqual((p.works, p.signed_by), (8, ""))

    def test_a_collection_becomes_a_signed_dp1_playlist(self):
        c = [c for c in B.find("drift", get=self.src)[0] if c.vendor == "artblocks"][0]
        p = B.build(c, limit=4, key=self.key, get=self.src)
        doc = p.request()["dp1_call"]
        self.assertEqual((doc["dpVersion"], doc["title"], len(doc["items"])), ("1.1.0", "Drift by Ada Example", 4))
        item = doc["items"][0]
        self.assertEqual(item["title"], "Drift #0")
        self.assertEqual(item["provenance"], {"type": "onChain", "contract": {"chain": "evm", "address": PROJECT[:42],
                                                                              "standard": "erc721", "tokenId": "7000000"}})
        self.assertEqual(set(item), {"id", "title", "source", "duration", "license", "provenance"})
        self.assertEqual([s["verdict"] for s in signing.verify(doc)], ["valid"])
        self.assertEqual((doc["signatures"][0]["role"], doc["curators"][0]["key"]), ("curator", self.key.did))

    def test_an_artist_by_name_uses_the_resolver_when_it_knows_more_pieces(self):
        artist = B.find("ada example", get=self.src)[0][0]
        self.src.raster_works = 9
        p = B.build(artist, limit=6, key=None, get=self.src)
        self.assertEqual((p.source, p.works, p.title), ("resolver:artist:42", 6, "Works by Ada Example"))
        self.assertEqual(p.document["items"][0]["duration"], B.DURATION["video"])
        self.src.raster_works = 1                                    # no more variety than the vendor's own: keep it
        self.assertEqual(B.build(artist, limit=6, get=self.src).source, f"artblocks:artist:{ARTIST}")
        self.src.down = {"ff-source-resolver-service.feral-file.workers.dev"}
        p = B.build(artist, limit=6, get=self.src)
        self.assertEqual((p.works, len(p.skipped)), (6, 1))

    def test_only_https_and_ipfs_sources_reach_a_playlist(self):
        p = B.build(B.find("tides", get=self.src)[0][0], get=self.src)
        self.assertEqual([i["title"] for i in p.document["items"]], ["Tide"])
        self.assertEqual(p.document["items"][0]["source"], V.IPFS_GATEWAY + "Qm" + "a" * 44)
        for bad in ("http://x.example/a", "javascript:alert(1)", "file:///etc/passwd", "https://user@x.example/a",
                    "ipfs://../../etc", "data:text/html,x", "", None, 5):
            self.assertEqual(V.https(bad), "", bad)

    def test_limits_and_refs(self):
        c = B.parse_ref(f"artblocks:collection:{PROJECT}")
        for bad in (0, 101, True, "5"):
            with self.assertRaises(ValueError):
                B.build(c, limit=bad, get=self.src)
        self.assertEqual(B.build(c, limit=100, get=self.src).works, 100)
        for bad in ("artblocks", "raster:artist:1", "feed:playlist:../x", "objkt:thing:KT1"):
            with self.assertRaises(ValueError):
                B.parse_ref(bad)
        with self.assertRaises(V.VendorError):
            B.build(B.parse_ref("artblocks:collection:not-an-id"), get=self.src)

    def test_nothing_but_the_four_sources_is_ever_fetched(self):
        for url in ("https://example.com/x", "http://feed.feralfile.com/api/v1/playlists", "https://169.254.169.254/",
                    "https://feed.feralfile.com.evil.example/", "file:///etc/passwd"):
            with self.assertRaises(V.VendorError):
                V.fetch(url)
        self.assertEqual(V.HOSTS, {"feed.feralfile.com", "data.artblocks.io", "data.objkt.com",
                                   "ff-source-resolver-service.feral-file.workers.dev"})


class Signing(unittest.TestCase):
    def test_a_playlist_the_public_feed_signed_verifies(self):
        """The contract dp1-js and ff-cli sign by: JCS, a newline, SHA-256, Ed25519 over the digest, did:key."""
        doc = json.loads((ROOT / "tests/data/feed-signed.json").read_text())
        verdicts = {v["role"]: v["verdict"] for v in signing.verify(doc)}
        self.assertEqual(verdicts, {"curator": "unchecked", "feed": "valid"})        # the curator's is eip191
        doc["title"] += "!"
        self.assertEqual([v["verdict"] for v in signing.verify(doc)], ["unchecked", "invalid"])

    def test_canonical_form(self):
        self.assertEqual(signing.canonical({"b": [1, 2.0, 1.5, True, None], "a": "é\n\"", "€": 1, "A": 1e21}),
                         '{"A":1e+21,"a":"é\\n\\"","b":[1,2,1.5,true,null],"€":1}')
        for bad in (float("nan"), 2 ** 60, {1: 2}, object()):
            with self.assertRaises(signing.SigningError):
                signing.canonical(bad)

    def test_the_key_file_is_made_once_private_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub/key"
            key = signing.Key.at(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(signing.Key.at(path).did, key.did)
            self.assertTrue(key.did.startswith("did:key:z6Mk"))
            self.assertEqual(signing.public_of(key.did), signing.public_of(signing.Key.parse(key.export()).did))
        doc = key.sign({"dpVersion": "1.1.0", "title": "t", "items": [{"source": "https://x.example/1"}]},
                       now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
        self.assertEqual((doc["signatures"][0]["ts"], signing.verify(doc)[0]["verdict"]), ("2026-01-02T03:04:05Z", "valid"))
        with self.assertRaises(signing.SigningError):
            key.sign(doc, "owner")
        for bad in ("not a key!", "abcd", "AAAA"):
            with self.assertRaises(signing.SigningError):
                signing.Key.parse(bad)


class CommandLine(unittest.TestCase):
    def test_help_and_bad_input_do_not_reach_a_source(self):
        with self.assertRaises(SystemExit):
            main(["build", "--limit", "x"])
        self.assertEqual(main(["build"]), 1)
        self.assertEqual(main(["build", "--ref", "nope"]), 1)


if __name__ == "__main__":
    unittest.main()
