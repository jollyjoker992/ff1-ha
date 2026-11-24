"""From a few words, a link or a wallet address to something an FF1 can play.

`find(query)` asks every source and ranks what they know; `build(candidate)` turns the chosen one into either

- a **playlist URL**, when the feed already holds a signed DP-1 playlist for it (the curator's own document is
  better than one rebuilt here: it is played as signed), or
- a **DP-1 playlist** made here from the vendor's works (spec v1.1.0: `dpVersion`, `title`, `items` with
  `source`, `duration`, `provenance`), signed as `curator` with this installation's key when one is given.

"Best" is decided here, not by the caller: how well the title or artist matches what was asked, then the
source (a curated, signed feed playlist; then the vendor that owns the collection; then the resolver, which
sees an artist across platforms), then size. For an artist found by name the builder also asks the resolver
with that artist's wallet address, because Raster lists the works of every platform, and uses that when it
knows more different pieces than the vendor that named the artist (many mints of one series count as one).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .signing import Key
from .vendors import VENDORS, Candidate, Fetch, VendorError, Work, fetch

DP_VERSION = "1.1.0"
MAX_ITEMS = 100
DEFAULT_ITEMS = 30
DURATION = {"web": 600, "video": 300, "image": 120, "": 300}     # seconds on screen, by kind of work
VENDOR_RANK = {"feed": 3, "artblocks": 2, "objkt": 2, "resolver": 1}


@dataclass(frozen=True)
class Playlist:
    """What `build` gives: `url` (a signed feed playlist to play as it is) or `document` (a DP-1 playlist)."""
    title: str
    source: str                               # the candidate it came from, as vendor:kind:id
    works: int
    url: str = ""
    document: dict[str, Any] | None = None
    signed_by: str = ""
    skipped: tuple[str, ...] = ()             # sources that failed, so the caller can say what was not asked

    def request(self) -> dict[str, Any]:
        """The `request` of the FF1's `displayPlaylist` command."""
        return {"playlistUrl": self.url} if self.url else {"dp1_call": self.document}


def _match(query: str, text: str) -> int:
    q, t = query.lower().strip(), text.lower().strip()
    if not q or not t:
        return 0
    return 4 if t == q else 3 if t.startswith(q) else 2 if q in t else 1 if all(w in t for w in q.split()) else 0


def _score(query: str, c: Candidate) -> tuple[int, int, int]:
    """How well it answers what was asked, then how good the source is, then how much it holds."""
    if c.extra.get("exact"):
        best = 4
    elif c.vendor == "resolver" or re.match(r"^(0x|tz|KT1|https://)", query):
        best = 3                               # an exact link or address is an exact answer
    elif c.kind == "artist":
        best = _match(query, c.title)
    else:                                      # a collection named for it beats one that is only by that artist
        best = max(_match(query, c.title), _match(query, c.artist) - 1)
    return best, VENDOR_RANK.get(c.vendor, 0), min(c.size or 0, MAX_ITEMS)


def find(query: str, vendors: list[str] | None = None, get: Fetch = fetch) -> tuple[list[Candidate], list[str]]:
    """Every candidate the sources know for `query`, best first, and the sources that failed."""
    query = (query or "").strip()
    if not 2 <= len(query) <= 300:
        raise ValueError("a name, a link or an address, 2 to 300 characters")
    found: list[Candidate] = []
    failed: list[str] = []
    for name in vendors or list(VENDORS):
        if name not in VENDORS:
            raise ValueError(f"vendors are {', '.join(VENDORS)}")
        try:
            found += VENDORS[name](get).search(query)
        except VendorError as e:
            failed.append(f"{name}: {e}")
    for work in [c for c in found if c.vendor == "resolver" and c.kind == "work" and c.extra.get("series")]:
        series = work.extra["series"]          # a link to one work of a series: the series itself, where a vendor has it
        for name in (n for n in vendors or list(VENDORS) if n != "resolver"):
            try:
                found += [replace(c, extra={**c.extra, "exact": "1"}) for c in VENDORS[name](get).search(series)
                          if c.title.lower() == series.lower() and (c.vendor == "feed" or not work.artist
                                                                    or c.artist.lower() == work.artist.lower())]
            except VendorError as e:
                failed.append(f"{name}: {e}")
    ranked = sorted((c for c in found if _score(query, c)[0] > 0), key=lambda c: _score(query, c), reverse=True)
    return ranked, failed


def parse_ref(ref: str) -> Candidate:
    """A candidate from its `vendor:kind:id` (what `find` printed), to build without searching again."""
    vendor, _, rest = ref.partition(":")
    kind, _, ident = rest.partition(":")
    if vendor not in VENDORS or kind not in ("collection", "artist", "playlist", "work") or not ident:
        raise ValueError("a reference is vendor:kind:id, as find prints it")
    if vendor == "feed":
        if not re.fullmatch(r"[0-9a-f-]{36}", ident):
            raise ValueError("a feed playlist is named by its id")
        return Candidate(vendor, kind, ident, "", url=f"https://feed.feralfile.com/api/v1/playlists/{ident}")
    return Candidate(vendor, kind, ident, "")


def _item(w: Work) -> dict[str, Any]:
    item: dict[str, Any] = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, w.source)), "title": w.title[:200] or "Untitled",
                            "source": w.source, "duration": DURATION.get(w.kind, DURATION[""]), "license": "open"}
    if w.chain and w.contract:
        contract = {"chain": w.chain, "address": w.contract}
        if w.standard:
            contract["standard"] = w.standard
        if w.token_id:
            contract["tokenId"] = w.token_id
        item["provenance"] = {"type": "onChain", "contract": contract}
    return item


def document(title: str, works: list[Work], now: datetime | None = None) -> dict[str, Any]:
    if not works:
        raise VendorError("no work with a source the FF1 can load")
    seen, items = set(), []
    for w in works:
        if w.source not in seen:
            seen.add(w.source)
            items.append(_item(w))
    return {"dpVersion": DP_VERSION, "id": str(uuid.uuid4()), "title": title[:200],
            "created": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "items": items}


def _variety(works: list[Work]) -> int:
    """How many different pieces, counting the mints of one series ("Drift #1", "Drift #2") as one."""
    return len({re.sub(r"\s*#\d+$", "", w.title).lower() for w in works})


def build(candidate: Candidate, limit: int = DEFAULT_ITEMS, key: Key | None = None, get: Fetch = fetch) -> Playlist:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ITEMS:
        raise ValueError(f"limit is 1 to {MAX_ITEMS}")
    if candidate.vendor == "feed":
        doc = get(candidate.url)
        if not isinstance(doc, dict) or not doc.get("items") or not doc.get("signatures"):
            raise VendorError("feed: that playlist is gone, empty or unsigned")
        return Playlist(str(doc.get("title") or candidate.title), candidate.ref, len(doc["items"]), url=candidate.url)
    skipped: list[str] = []
    works = VENDORS[candidate.vendor](get).works(candidate, limit)
    title = candidate.title or (works[0].artist if candidate.kind == "artist" and works else "") or "Playlist"
    source = candidate
    address = candidate.extra.get("address", "")
    if candidate.kind == "artist" and candidate.vendor != "resolver" and address:
        try:                                   # the same artist across platforms, when Raster knows them
            resolver = VENDORS["resolver"](get)
            for artist in resolver.artists(address)[:1]:
                wider = resolver.works(artist, limit)
                if _variety(wider) > _variety(works):
                    works, source = wider, replace(artist, title=artist.title or title)
        except VendorError as e:
            skipped.append(f"resolver: {e}")
    if candidate.kind == "artist" or (candidate.artist and candidate.artist.lower() not in title.lower()):
        by = candidate.artist or title
        title = f"Works by {by}" if candidate.kind == "artist" else f"{title} by {by}"
    doc = document(title, works)
    signed_by = ""
    if key is not None:
        doc["curators"] = [{"name": "ff1-playlist", "key": key.did}]
        doc = key.sign(doc, "curator")
        signed_by = key.did
    return Playlist(title, source.ref, len(doc["items"]), document=doc, signed_by=signed_by, skipped=tuple(skipped))
