"""Build a playlist for an FF1 from public art sources. Output is JSON.

    python -m ff1_playlist find "<artist, collection, link or wallet address>" [--vendor NAME ...]
        what the sources know, best first: each with a `ref` to build from
    python -m ff1_playlist build "<the same>" [--limit N] [--out FILE]
        the best match, built: {"request": ...} is what the FF1's displayPlaylist takes
    python -m ff1_playlist build --ref vendor:kind:id [--limit N] [--out FILE]
        one candidate of `find`, by its ref
    python -m ff1_playlist key
        this installation's signing identity (did:key); made on first use
    python -m ff1_playlist verify FILE
        the signatures of a DP-1 playlist

The signing key is the file named by FF1_PLAYLIST_KEY (default ~/.config/ff1-playlist/key): base64 PKCS#8 DER,
as ff-cli keeps its own. `--unsigned` builds without it. Nothing here talks to an FF1.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from .builder import DEFAULT_ITEMS, MAX_ITEMS, build, find, parse_ref
from .signing import Key, SigningError, verify
from .vendors import VENDORS, VendorError


def key_path() -> Path:
    return Path(os.environ.get("FF1_PLAYLIST_KEY") or Path.home() / ".config/ff1-playlist/key")


def run(args: argparse.Namespace) -> dict:
    if args.cmd == "key":
        return {"did": Key.at(key_path()).did, "file": str(key_path())}
    if args.cmd == "verify":
        return {"signatures": verify(json.loads(Path(args.file).read_text()))}
    if args.cmd == "find":
        found, failed = find(args.query, args.vendor)
        return {"candidates": [{"ref": c.ref, "vendor": c.vendor, "kind": c.kind, "title": c.title, "artist": c.artist,
                                "works": c.size} for c in found[:20]], "failed": failed}
    failed: list[str] = []
    if args.ref:
        candidate = parse_ref(args.ref)
    elif args.query:
        found, failed = find(args.query, args.vendor)
        if not found:
            return {"error": "nothing found", "failed": failed}
        candidate = found[0]
    else:
        raise ValueError("build needs a query or --ref")
    playlist = build(candidate, args.limit, None if args.unsigned else Key.at(key_path()))
    out = {"title": playlist.title, "from": playlist.source, "works": playlist.works, "signed_by": playlist.signed_by,
           "failed": [*failed, *playlist.skipped], "request": playlist.request()}
    if args.out:
        Path(args.out).write_text(json.dumps(playlist.request(), ensure_ascii=False))
        out["request"] = {"file": args.out}
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ff1-playlist", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("query")
    b = sub.add_parser("build")
    b.add_argument("query", nargs="?")
    b.add_argument("--ref")
    b.add_argument("--limit", type=int, default=DEFAULT_ITEMS, help=f"works to include, up to {MAX_ITEMS}")
    b.add_argument("--out")
    b.add_argument("--unsigned", action="store_true")
    for s in (f, b):
        s.add_argument("--vendor", action="append", choices=list(VENDORS))
    sub.add_parser("key")
    sub.add_parser("verify").add_argument("file")
    args = p.parse_args(argv)
    try:
        print(json.dumps(run(args), indent=1, ensure_ascii=False, default=dataclasses.asdict))
    except (VendorError, SigningError, ValueError, OSError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
