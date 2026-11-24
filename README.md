# ff1-ha

The Feral File FF1 at home: a client of its LAN interface, a Home Assistant integration built on it, and a
builder of DP-1 playlists from public art sources. The FF1 is the
art computer behind a screen; its operating system (FF OS, [`feral-file/ffos-user`](https://github.com/feral-file/ffos-user))
serves an HTTP interface on port 1111 to the network it is on.

| Part | What |
|---|---|
| `docs/FF1-LAN.md` | What the FF1 serves on its LAN, who may do what there, and what this repository uses of it |
| `src/ff1_lan/` | The SDK: async (aiohttp only), status, the reads, the controls, the notification socket |
| `src/ff1_playlist/` | The playlist builder: finds an artist, a collection or a work in four public sources and makes a signed [DP-1](https://github.com/display-protocol/dp1) playlist. Standard library only (signing needs `cryptography`) |
| `.agents/skills/ff1-playlists/` | The skill that tells an assistant how to use the builder |
| `custom_components/ff1/` | The Home Assistant integration: a media player, sensors, the screen's settings, the sleep schedule |
| `tests/` | unittest: a made-up FF1 on 127.0.0.1 that keeps the device's own rules; canned answers of the art sources; a playlist the public feed signed |
| `tests_ha/` | the integration inside Home Assistant |

It holds **no key and no account**. It can do what any phone on the same Wi-Fi can: the device's *LAN-open
set*, which is what the next command can undo. What is the owner's alone (a shell, a factory reset, Wi-Fi
setup, an update, shutdown, the content policy, adding controllers) needs a signed request from a paired
controller, which this client never sends; use the Feral File app or
[`ff-cli`](https://github.com/feral-file/ff-cli) for those.

## The SDK

```python
async with aiohttp.ClientSession() as s:
    ff1 = Ff1Client(s, "192.0.2.10")
    status = await ff1.status()            # identity, firmware, whether the LAN may send commands
    player = await ff1.player()            # player.now: title, artist, thumbnail
    await ff1.set_volume(20)
    await ff1.sleep()                      # dark screen until the schedule's wake time, or wake()
    async for kind, message in ff1.notifications():   # device_status, player_status, ddc_status, …
        ...
```

```sh
pip install -e .                           # or: PYTHONPATH=src python -m ff1_lan ...
python -m ff1_lan --host 192.0.2.10 status
python -m ff1_lan --host 192.0.2.10 now
```

Find the address with `dns-sd -B _ff1._tcp` (macOS) or `avahi-browse -r _ff1._tcp`: the FF1 advertises
itself, with its device id and name.

## The playlist builder

```sh
python -m ff1_playlist find "tyler hobbs"          # what the sources know, best first
python -m ff1_playlist build "fidenza"             # -> {"request": {"playlistUrl": ...}}: Art Blocks' own signed playlist
python -m ff1_playlist build "tyler hobbs" --limit 20   # -> {"request": {"dp1_call": {...}}}: built and signed here
```

| Source | Asked for | Key |
|---|---|---|
| The DP-1 feed (`feed.feralfile.com`) | playlists curated and signed by Feral File, Art Blocks and objkt, by title; only those three channels, because anyone may publish to the feed | none |
| Art Blocks (`data.artblocks.io`) | projects by name or artist, and their mints | none |
| objkt (`data.objkt.com`) | Tezos collections and artists by name or address | none |
| Feral File's source resolver | what a link names; an artist's works across platforms, by wallet address (it reads Raster, whose own API needs a key) | none today |

It asks all four and takes the best: an exact title before a partial one, a curated and signed feed playlist
(played by its URL, untouched) before a list built here, the collection's own platform before the rest. For an
artist found by name it asks the resolver with that artist's address and uses Raster's list when it holds more
different pieces. A source that is down is named in `failed` and the others still answer. Nothing but these
four hosts is ever fetched, and only `https` (or `ipfs://` through a gateway) sources reach a playlist.

A built playlist follows DP-1 v1.1.0 (`dpVersion`, `title`, `items` with `source`, `duration`, `license`,
on-chain `provenance`) and is signed as `curator` the way `dp1-js` and `ff-cli` sign: SHA-256 over the JCS
canonical form plus a newline, Ed25519 over that digest, `kid` a `did:key`. The key is made on first use at
`FF1_PLAYLIST_KEY` (default `~/.config/ff1-playlist/key`, mode 0600, base64 PKCS#8 DER as ff-cli keeps its
own). The builder never talks to an FF1: `request` is what the device's `displayPlaylist` takes, sent by
whatever controls the device (`Ff1Client.show_playlist`, or a gateway in front of it).

What this does not close: the resolver answers without a key today but its code expects one from its clients,
so that may change (the other three then still work, without the cross-platform artist lists); a work's
`source` is whatever the vendor recorded, on any https host; a built playlist is not published anywhere.

### Why not ff-cli

[`ff-cli`](https://github.com/feral-file/ff-cli) builds, signs and displays DP-1 playlists too, and is the
tool to use on a computer. It was not used here because: it builds from a link, coordinates or a wallet, not
from a name; it has no Art Blocks or objkt lookup of its own (it asks the resolver with the client key its
npm package ships); it needs Node.js 22 and a native keyring; and one binary also carries `ssh`, pairing,
publishing and reports to Feral File's support, which an assistant that only picks art must not hold. The
signing here is the same contract, so playlists from either verify with the other.

## The Home Assistant integration

Copy or mount `custom_components/ff1` into `/config/custom_components/`, and install this package
(`pip install .`) in Home Assistant's environment: the integration imports `ff1_lan` and declares no PyPI
requirement. Then add "Feral File FF1" with the device's address. Home Assistant must reach the FF1 from an
address the device counts as local (a private address: a Docker bridge behind the host is one).

What it gives, per FF1 (entity ids are fixed: `<platform>.<device id>_<key>`, as `sensor.ff1_abcd1234_now_showing`):

- **media player** `…_player`: the work on screen (title, artist, picture), volume and mute, next and
  previous work, "off" = the device's sleep mode (dark screen, still answering) and "on" = wake. No pause:
  an FF1 has none.
- **sensors**: now showing, firmware, CPU and GPU temperature, memory and disk used, Wi-Fi signal, artwork
  cache used, works needing attention (with which and why), network outages in 24 h and the last one, last
  boot, the next sleep or wake, orientation.
- **binary sensors**: internet, the remote control relay, control from the home network (the owner's LAN
  switch), sleeping, firmware update waiting, artwork problem (a broken work in the playlist, or the one on
  screen failed to render).
- **numbers**: the screen's brightness, contrast and volume over DDC, for what the panel answers.
- **switch**: the sleep schedule. **buttons**: reload artwork, default playlist, restart.
- **services** on the media player: `ff1.show_playlist` (a signed playlist's URL or a DP-1 playlist from the
  builder), `ff1.rotate`, `ff1.set_sleep_schedule` (on or off, sleep and wake times).

It listens to the device's notification socket, so a new work or a volume change shows at once, and polls
every minute as the safety net (the slow reads every ten). When the owner closes LAN control in the Feral
File app, the connectivity sensors keep working and every control becomes unavailable.

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install aiohttp
.venv/bin/python -m unittest discover -s tests          # the SDK against the made-up FF1

# the integration inside Home Assistant (Python 3.14; the harness pins its own Home Assistant)
python3.14 -m venv .venv-ha && .venv-ha/bin/pip install pytest-homeassistant-custom-component
.venv-ha/bin/python -m pytest
```

Unofficial: not made by Feral File. The LAN interface is the device's own, documented in its repository.
