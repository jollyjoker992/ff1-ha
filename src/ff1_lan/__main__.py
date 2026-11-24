"""By hand: `python -m ff1_lan --host <address> status|now|health|sleep|wake|next|previous|volume <0-100>`."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys

import aiohttp

from . import Ff1Client, Ff1Error


async def run(args: argparse.Namespace) -> object:
    async with aiohttp.ClientSession() as session:
        ff1 = Ff1Client(session, args.host, args.port)
        match args.what:
            case "status":
                return {"status": await ff1.status(), "device": await ff1.device(), "metrics": await ff1.metrics()}
            case "now":
                player = await ff1.player()
                return {"now": player.now, "of": len(player.works), "render": player.render, "sleeping": player.sleeping}
            case "health":
                return {"media": await ff1.media_health(), "cache": await ff1.cache(), "panel": await ff1.panel()}
            case "sleep":
                return await ff1.sleep()
            case "wake":
                return await ff1.wake()
            case "next":
                return await ff1.step(+1)
            case "previous":
                return await ff1.step(-1)
            case "volume":
                await ff1.set_volume(int(args.value))
                return (await ff1.device()).volume
    return None


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def main() -> int:
    p = argparse.ArgumentParser(prog="ff1-lan", description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=1111)
    p.add_argument("what", choices=["status", "now", "health", "sleep", "wake", "next", "previous", "volume"])
    p.add_argument("value", nargs="?")
    args = p.parse_args()
    try:
        print(json.dumps(_plain(asyncio.run(run(args))), indent=2, ensure_ascii=False))
    except Ff1Error as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
