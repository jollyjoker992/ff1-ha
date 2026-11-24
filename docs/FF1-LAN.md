# The FF1 on the home network

Read from `feral-file/ffos-user` (`docs/api-design.md`, `docs/ff1-control-v1.md`,
`docs/controld-inbound-controller-messages.md`, `components/feral-controld/control/tiers.go`) and checked
against an FF1 on FF OS 3.0.1. The device's repository is the authority; this is what matters to a home.

## What it serves

`feral-controld` listens on `0.0.0.0:1111` and advertises `_ff1._tcp` over mDNS (TXT `id`, `name`, `claimed`,
`api`). Every route first checks that the caller is **local**: loopback, a private or link-local address,
CGNAT (Tailscale), or on one of the device's own subnets. Anything else gets `403 lan_only`.

| Route | What | On an owned device |
|---|---|---|
| `GET /api/v2/status` | device id, firmware, branch, claimed, internet, LAN link, relay state, the control block (state, the owner's LAN switch, the device's public keys) | always answers |
| `POST /api/cast` | `{"command", "request"}`: the commands the Feral File app sends | the LAN-open set in plaintext while the owner's switch is open; everything else needs a signed envelope |
| `GET /api/notification` (WebSocket) | `device_status`, `player_status`, `ddc_status`, `offline_cache_status` as they change, and copies sealed for each paired controller | plaintext only while the switch is open |
| `GET /metrics` | Prometheus text: playback counters, Wi-Fi signal, link state, relay state, control state | always answers |
| `GET /api/screenshot` | a PNG of what the browser renders | refused (`403 enrollment_required`): no signed form exists |

## Who may do what

The device's rule: **on its own network, anyone may do what the next command can undo. What cannot be undone
is the owner's alone, by a signed request, on every route.**

| | Commands |
|---|---|
| LAN-open (also what a paired *controller* may do) | reads: `getDeviceStatus`, `deviceMetrics`, `checkStatus`, `ddcPanelStatus`, `getOfflineCacheStatus`, `getMediaHealth`, `getRecentlyPlayed`, `getContentPolicy`, `getSeriesPlayback`. Playback: `displayPlaylist`, `displayDefaultPlaylist`, `playRecentlyPlayed`, `moveToArtwork`, `refreshArtwork`, `refreshPlaylist`, shuffle, loop, framing. Device: `setVolume`, `toggleMute`, `rotate`, `ddcPanelControl`, `sleepNow`, `wakeNow`, `setSleepSchedule`, `reboot`, `runNetworkDiagnostics`, `uploadLogs`. The offline cache: download and clear |
| Owner only | `sshAccess`, `factoryReset`, `startWifiSetup`, `updateToLatestVersion`, `shutdown`, `setDeviceName`, `setContentPolicy`, `setSeriesPlayback`, keyboard and pointer input, adding, listing and revoking controllers, `setLanControl`, anything the device does not know |

- The owner can close the LAN to plaintext altogether (`setLanControl`, in the Feral File app). Then only
  paired controllers get in, and this client reads status and metrics only.
- A `locked` device (its control state damaged) refuses everything but status.
- The device sheds commands when flooded (`429`): nothing was applied.
- There is no pause, and no "next" command: the next work is `moveToArtwork` with an index.
- "Sleep" is the player's sleep mode (a dark page) plus the panel's standby over DDC when the panel takes it;
  the computer stays on and answers. `shutdown` is the one power command the LAN cannot send: the device
  stays off until someone presses its button.

## Pairing, and why this repository does without it

A paired controller holds its own P-256 keys and a grant on the device (role *owner* or *controller*). Each
request is signed (ES256) and encrypted (ECDH-ES + A256GCM) to the device; each reply is signed by the
device. A new controller shows its public keys as a QR (`ff1-ctl:1|…`), an owner scans it with **Add
controller** in the Feral File app, the app registers it on the FF1 and answers over the relay, and the
controller enrolls. `ff-cli device pair <name>` does this and keeps the keys in
`~/.config/ff-cli/controllers/<device id>.json`.

What a *controller* grant adds to the LAN-open set: nothing in commands. It adds reach (commands when the
owner closed the LAN, or from outside the home through the relay) and the sealed notifications. What an
*owner* grant adds is everything above that cannot be undone.

So for a home server on the same network as the FF1, with the LAN switch open, pairing buys nothing and
costs a private key to keep. This client stays without one. If the owner closes the LAN switch, the path is
a controller grant: an envelope transport beside the plaintext one in `client.py`, checked against the
conformance fixtures in `components/feral-controld/control/testdata/fixtures/` of ffos-user.

## What this does not close

- Anyone on the home Wi-Fi can do everything this client can, with or without it: that is the device's
  design, and only the owner's LAN switch changes it.
- A reboot, a rotation or a panel power-off over DDC is "undoable" by the device's test but not harmless: a
  rotated wall piece, or a panel that will not wake over DDC, needs a person. Callers decide which of the
  controls they offer.
- `displayPlaylist` by URL makes the device fetch that URL. The SDK takes https only; what may be displayed
  is the device's content policy, set by its owner.
- Replies are not authenticated in plaintext: another machine on the LAN that took the device's address
  could answer in its place. The integration checks the device id, which such a machine could copy.
