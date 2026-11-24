"""The four sources a playlist is built from. None asks a key or an account; each is called over https only,
and nothing else is ever called (`HOSTS`): a link or a name someone typed can never make this code fetch
another address.

| Vendor | What it knows | How it is asked |
|---|---|---|
| `feed` | the DP-1 feed (feed.feralfile.com): playlists already curated and signed by Feral File, Art Blocks, objkt | by a playlist's title, within the named channels only (`FEED_CHANNELS`: anyone may publish to the feed) |
| `artblocks` | Art Blocks' own index (data.artblocks.io): every project and token | by project name or artist name |
| `objkt` | objkt's index of Tezos (data.objkt.com): collections and artists | by collection name or artist alias |
| `resolver` | Feral File's source resolver (it reads Raster for its clients): what a link names, an artist's works across platforms | by a work's or series' link, or an artist's wallet address |

A vendor answers `search(query)` with candidates and `works(candidate, limit)` with the works of one. A vendor
that is down or changed its answer raises `VendorError`; the caller goes on with the others.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

FEED = "https://feed.feralfile.com/api/v1"
ARTBLOCKS = "https://data.artblocks.io/v1/graphql"
OBJKT = "https://data.objkt.com/v3/graphql"
RESOLVER = "https://ff-source-resolver-service.feral-file.workers.dev"
HOSTS = frozenset(urllib.parse.urlsplit(u).hostname for u in (FEED, ARTBLOCKS, OBJKT, RESOLVER))
FEED_CHANNELS = {"feral-file-2cf655bd": "Feral File", "artblocks": "Art Blocks", "objkt": "objkt.com"}
IPFS_GATEWAY = "https://ipfs.filebase.io/ipfs/"
USER_AGENT = "ff1-playlist/0.2 (+https://github.com/jollyjoker992/ff1-ha)"
MAX_BYTES = 8 << 20
ADDRESS = re.compile(r"^(0x[0-9a-fA-F]{40}|(tz[1-4]|KT1)[1-9A-HJ-NP-Za-km-z]{33})$")


class VendorError(Exception):
    """A source did not answer, or not in the shape it used to."""


@dataclass(frozen=True)
class Work:
    title: str
    source: str                               # https; what the FF1 loads
    artist: str = ""
    thumbnail: str = ""
    kind: str = ""                            # web, image, video, "" when the vendor does not say
    chain: str = ""                           # evm, tezos
    contract: str = ""
    token_id: str = ""
    standard: str = ""


@dataclass(frozen=True)
class Candidate:
    """Something a playlist can be made of: a collection, an artist's works, or a playlist that already exists."""
    vendor: str
    kind: str                                 # collection, artist, playlist
    id: str                                   # the vendor's own id; with `vendor`, enough to build from later
    title: str
    artist: str = ""
    size: int | None = None
    url: str = ""                             # for `playlist`: the signed DP-1 document, played as it is
    extra: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def ref(self) -> str:
        return f"{self.vendor}:{self.kind}:{self.id}"


# ---------- transport: these hosts, https, JSON, a size cap ----------
def fetch(url: str, body: dict[str, Any] | None = None, timeout: float = 30) -> Any:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise VendorError(f"{parts.hostname or url} is not one of the sources")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise VendorError(f"{parts.hostname}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise VendorError(f"{parts.hostname}: {type(e).__name__}") from e
    if len(raw) > MAX_BYTES:
        raise VendorError(f"{parts.hostname}: the answer is too large")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise VendorError(f"{parts.hostname}: the answer is not JSON") from e


Fetch = Callable[..., Any]


def _graphql(get: Fetch, url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    reply = get(url, {"query": query, "variables": variables})
    if not isinstance(reply, dict) or reply.get("errors") or not isinstance(reply.get("data"), dict):
        raise VendorError(f"{urllib.parse.urlsplit(url).hostname}: the query was refused")
    return reply["data"]


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def https(url: Any) -> str:
    """An https URL, an ipfs:// one through the gateway, or nothing: no other scheme reaches a playlist."""
    url = _s(url)
    if url.startswith("ipfs://"):
        path = url[len("ipfs://"):].removeprefix("ipfs/")
        return IPFS_GATEWAY + urllib.parse.quote(path, safe="/?=&%") if re.match(r"^[A-Za-z0-9]{40,}", path) else ""
    parts = urllib.parse.urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname and not parts.username and " " not in url else ""


def _like(query: str) -> str:
    return "%" + re.sub(r"[%_\\]", " ", query).strip() + "%"


# ---------- Art Blocks ----------
class ArtBlocks:
    name = "artblocks"

    def __init__(self, get: Fetch = fetch) -> None:
        self.get = get

    def search(self, query: str) -> list[Candidate]:
        if ADDRESS.match(query) or query.startswith("http"):
            return []
        data = _graphql(self.get, ARTBLOCKS, """query($q:String!){projects_metadata(where:{chain_id:{_eq:1},
            invocations:{_gt:"0"},_or:[{name:{_ilike:$q}},{artist_name:{_ilike:$q}}]},order_by:{invocations:desc},limit:12)
            {id name artist_name artist_address invocations}}""", {"q": _like(query)})
        out, artists = [], {}
        for p in data.get("projects_metadata") or []:
            if _s(p.get("id")) and _s(p.get("name")):
                out.append(Candidate(self.name, "collection", p["id"], p["name"], _s(p.get("artist_name")),
                                     int(p.get("invocations") or 0), extra={"address": _s(p.get("artist_address"))}))
                address, artist = _s(p.get("artist_address")).lower(), _s(p.get("artist_name"))
                if re.fullmatch(r"0x[0-9a-f]{40}", address) and artist and query.lower().strip() in artist.lower():
                    artists.setdefault(address, [artist, 0])[1] += int(p.get("invocations") or 0)
        out += [Candidate(self.name, "artist", address, name, name, size, extra={"address": address})
                for address, (name, size) in artists.items()]
        return out

    def _artist_works(self, c: Candidate, limit: int) -> list[Work]:
        """A few works of each of the artist's projects, the projects in the order they were made."""
        data = _graphql(self.get, ARTBLOCKS, """query($a:String!){projects_metadata(where:{chain_id:{_eq:1},
            artist_address:{_eq:$a},invocations:{_gt:"0"}},order_by:{project_id:asc},limit:40){id name artist_name}}""",
                        {"a": c.id})
        projects = [p for p in data.get("projects_metadata") or [] if _s(p.get("id"))]
        if not projects:
            return []
        each, out = max(1, limit // len(projects)), []
        for p in projects:
            if len(out) >= limit:
                break
            out += self.works(Candidate(self.name, "collection", p["id"], _s(p.get("name")), _s(p.get("artist_name"))),
                              min(each, limit - len(out)))
        return out

    def works(self, c: Candidate, limit: int) -> list[Work]:
        if c.kind == "artist" and re.fullmatch(r"0x[0-9a-f]{40}", c.id):
            return self._artist_works(c, limit)
        if not re.fullmatch(r"0x[0-9a-f]{40}-\d+", c.id):
            raise VendorError("not an Art Blocks project id")
        data = _graphql(self.get, ARTBLOCKS, """query($p:String!,$n:Int!){projects_metadata(where:{id:{_eq:$p}}){name artist_name}
            tokens_metadata(where:{project_id:{_eq:$p}},order_by:{invocation:asc},limit:$n)
            {token_id invocation contract_address live_view_url preview_asset_url}}""", {"p": c.id, "n": limit})
        project = (data.get("projects_metadata") or [{}])[0]
        name, artist = _s(project.get("name")) or c.title, _s(project.get("artist_name")) or c.artist
        out = []
        for t in data.get("tokens_metadata") or []:
            source = https(t.get("live_view_url"))
            if source:
                out.append(Work(f"{name} #{t.get('invocation')}", source, artist, https(t.get("preview_asset_url")), "web",
                                "evm", _s(t.get("contract_address")), _s(t.get("token_id")), "erc721"))
        return out


# ---------- objkt (Tezos) ----------
class Objkt:
    name = "objkt"
    TOKEN = "token_id name mime artifact_uri display_uri fa_contract fa{name} creators{holder{alias address}}"

    def __init__(self, get: Fetch = fetch) -> None:
        self.get = get

    def search(self, query: str) -> list[Candidate]:
        if query.startswith("http") or query.startswith("0x"):
            return []
        if ADDRESS.match(query):
            if not query.startswith("KT1"):
                return [Candidate(self.name, "artist", query, query, extra={"address": query})]
            data = _graphql(self.get, OBJKT, "query($a:String!){fa(where:{contract:{_eq:$a}}){name items creator{alias}}}",
                            {"a": query})
            return [Candidate(self.name, "collection", query, _s(f.get("name")) or query,
                              _s((f.get("creator") or {}).get("alias")), int(f.get("items") or 0))
                    for f in (data.get("fa") or [])[:1]]
        data = _graphql(self.get, OBJKT, """query($q:String!){
            fa(where:{name:{_ilike:$q},items:{_gt:0}},order_by:{items:desc_nulls_last},limit:8){contract name items creator{alias}}
            holder(where:{alias:{_ilike:$q}},limit:5){address alias}}""", {"q": _like(query)})
        out = [Candidate(self.name, "collection", _s(f.get("contract")), _s(f.get("name")),
                         _s((f.get("creator") or {}).get("alias")), int(f.get("items") or 0))
               for f in data.get("fa") or [] if _s(f.get("contract")) and _s(f.get("name"))]
        out += [Candidate(self.name, "artist", _s(h.get("address")), _s(h.get("alias")), _s(h.get("alias")),
                          extra={"address": _s(h.get("address"))})
                for h in data.get("holder") or [] if _s(h.get("address")) and _s(h.get("alias"))]
        return out

    def works(self, c: Candidate, limit: int) -> list[Work]:
        if not ADDRESS.match(c.id) or c.id.startswith("0x"):
            raise VendorError("not a Tezos address")
        where = "fa_contract:{_eq:$a}" if c.kind == "collection" else "creators:{creator_address:{_eq:$a}}"
        data = _graphql(self.get, OBJKT, "query($a:String!,$n:Int!){token(where:{%s,supply:{_gt:\"0\"}},"
                        "order_by:{pk:desc},limit:$n){%s}}" % (where, self.TOKEN), {"a": c.id, "n": limit})
        out = []
        for t in data.get("token") or []:
            mime = _s(t.get("mime"))
            source = https(t.get("artifact_uri")) or https(t.get("display_uri"))
            if not source or not _s(t.get("name")):
                continue
            kind = "video" if mime.startswith("video/") else "image" if mime.startswith("image/") else \
                "web" if mime in ("application/x-directory", "text/html") else ""
            artist = ", ".join(_s((x.get("holder") or {}).get("alias")) for x in t.get("creators") or []
                               if _s((x.get("holder") or {}).get("alias")))
            out.append(Work(_s(t.get("name")), source, artist or c.artist, https(t.get("display_uri")), kind, "tezos",
                            _s(t.get("fa_contract")), _s(t.get("token_id")), "fa2"))
        return out


# ---------- the DP-1 feed ----------
class Feed:
    name = "feed"

    def __init__(self, get: Fetch = fetch) -> None:
        self.get = get

    def search(self, query: str) -> list[Candidate]:
        if ADDRESS.match(query) or query.startswith("http"):
            return []
        words, out = query.lower().split(), []
        for slug, publisher in FEED_CHANNELS.items():
            cursor = ""
            for _ in range(5):                      # at most 500 playlists of a channel
                page = self.get(f"{FEED}/playlists?limit=100&channel={slug}" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""))
                if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                    raise VendorError("feed: the list is not in its usual shape")
                for p in page["items"]:
                    title, pid = _s(p.get("title")), _s(p.get("id"))
                    if title and re.fullmatch(r"[0-9a-f-]{36}", pid) and all(w in title.lower() for w in words) \
                            and p.get("signatures"):
                        out.append(Candidate(self.name, "playlist", pid, title, publisher,
                                             len(p.get("items") or []), f"{FEED}/playlists/{pid}"))
                cursor = _s(page.get("cursor"))
                if not page.get("hasMore") or not cursor:
                    break
        return out

    def works(self, c: Candidate, limit: int) -> list[Work]:
        raise VendorError("a feed playlist is played by its URL, as its curator signed it")


# ---------- Feral File's source resolver (Raster behind it) ----------
class Resolver:
    name = "resolver"
    KINDS = {"webapp": "web", "web": "web", "video": "video", "image": "image", "gif": "image", "svg": "image"}

    def __init__(self, get: Fetch = fetch) -> None:
        self.get = get

    def search(self, query: str) -> list[Candidate]:
        if ADDRESS.match(query) and not query.startswith("KT1"):
            return self.artists(query)
        if not query.startswith("https://"):
            return []
        reply = self.get(f"{RESOLVER}/work?url={urllib.parse.quote(query, safe='')}")
        work = reply.get("work") if isinstance(reply, dict) and reply.get("found") else None
        if not isinstance(work, dict) or not https(work.get("source")):
            return []
        title = _s(work.get("series")) or _s(work.get("title"))
        return [Candidate(self.name, "work", query, title, _s(work.get("artist")), 1, extra={
            k: _s(work.get(k)) for k in ("title", "source", "thumb", "media", "chain", "contract", "tokenId", "standard",
                                         "platform", "series")})]

    def artists(self, address: str) -> list[Candidate]:
        reply = self.get(f"{RESOLVER}/raster/artists?address={urllib.parse.quote(address, safe='')}")
        return [Candidate(self.name, "artist", _s(a.get("id")), _s(a.get("name")), _s(a.get("name")))
                for a in (reply.get("artists") if isinstance(reply, dict) else None) or []
                if re.fullmatch(r"\d+", _s(a.get("id"))) and _s(a.get("name"))]

    def works(self, c: Candidate, limit: int) -> list[Work]:
        if c.kind == "work":
            e = c.extra
            chain = {"ethereum": "evm", "tezos": "tezos"}.get(e.get("chain", ""), "")
            return [Work(e.get("title") or c.title, https(e.get("source")), c.artist, https(e.get("thumb")),
                         self.KINDS.get(e.get("media", ""), ""), chain, e.get("contract", ""), e.get("tokenId", ""),
                         e.get("standard", ""))]
        if not re.fullmatch(r"\d+", c.id):
            raise VendorError("not a resolver artist id")
        out: list[Work] = []
        after = ""
        while len(out) < limit:
            page = self.get(f"{RESOLVER}/raster/artist-works?id={c.id}&first=40" + (f"&after={urllib.parse.quote(after)}" if after else ""))
            if not isinstance(page, dict):
                raise VendorError("resolver: the works are not in their usual shape")
            for w in page.get("works") or []:
                token = ((w.get("tokens") or {}).get("nodes") or [{}])[0]
                media = token.get("media") or {}
                source = https(media.get("contentUrl"))
                if not source or not _s(w.get("title")):
                    continue
                chain = {"eip155:1": "evm", "tezos:NetXdQprcVkpaWU": "tezos"}.get(_s(token.get("chainId")), "")
                artist = ", ".join(_s(a.get("name")) for a in w.get("artists") or [] if _s(a.get("name")))
                out.append(Work(_s(w.get("title")), source, artist or c.artist, "",
                                self.KINDS.get(_s(media.get("contentType")).lower(), ""), chain,
                                _s(token.get("contractAddress")) if chain else "", _s(token.get("tokenId")) if chain else ""))
            after = _s(page.get("next"))
            if not after:
                break
        return out[:limit]


VENDORS: dict[str, type] = {v.name: v for v in (Feed, ArtBlocks, Objkt, Resolver)}
