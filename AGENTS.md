# Agent guide: ff1-ha

A client of the Feral File FF1's LAN interface and a Home Assistant integration. Read `README.md`, then
`docs/FF1-LAN.md` for what the device serves and who may do what.

## Rules

- No key, no account, no pairing: the SDK sends plaintext commands only, and only the ones in `READ` and
  `CONTROL` of `src/ff1_lan/client.py`. Both stay inside the device's LAN-open set (its controller tier,
  `control/tiers.go` in ffos-user). A command is added to `CONTROL` only if it passes the device's own test:
  the next command, or a wait, undoes it. Never an owner-tier command, never `ff1Envelope`.
- A command's arguments are checked before they are sent (`NotAllowed`), so a wrong value never reaches a device.
- Nothing the device says about a work's source leaves the SDK's models: a source URL can carry credentials.
  A work is its title, artist, thumbnail (https only) and id.
- Tests use the made-up FF1 only (`tests/fake_ff1.py`), on 127.0.0.1; it keeps the device's refusals, so a
  test of what is refused is as easy as one of what passes. Never a real FF1 in a test, and never a control
  sent to a real one without its owner's word (reads are harmless).
- The SDK needs `aiohttp` only, so Home Assistant can use it without installing anything.
- Entity ids are fixed (`entity.py`); other programs find them by name. Renaming one is a breaking change.
- A release is a tag `vX.Y.Z` with the version in `pyproject.toml`, `src/ff1_lan/__init__.py` and
  `custom_components/ff1/manifest.json` the same (`tests/test_release.py`). agentic-assistant pins the tag as
  a submodule.

## The playlist builder (`src/ff1_playlist`)

- Four sources only (`vendors.HOSTS`): the DP-1 feed, Art Blocks, objkt, Feral File's source resolver. No key,
  no account. `fetch` refuses every other host and every scheme but https; a new source is a decision of the
  owner, then a line there and a test.
- From the feed, only the channels in `FEED_CHANNELS` and only signed playlists: anyone may publish to it.
- A work's `source` is https, or `ipfs://` through the gateway, or the work is left out (`vendors.https`).
- Standard library only, so an assistant's container runs it as it is; `cryptography` only for signing.
- The signing contract is dp1-js's (`signing.py`); `tests/data/feed-signed.json`, a playlist the public feed
  signed, must keep verifying. The key file is never printed, logged or committed.
- The builder never talks to an FF1, and the FF1 client never calls a source.
- Tests use canned answers (`tests/test_playlist.py` `Sources`); never a real source in a test.

## Checks

```sh
.venv/bin/python -m unittest discover -s tests
.venv-ha/bin/python -m pytest          # the integration inside Home Assistant (see README.md)
```
