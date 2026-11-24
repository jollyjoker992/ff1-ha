"""Setup, entities, controls, the notification socket and the closed LAN, with a made-up FF1."""

import asyncio

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ff1.const import DOMAIN
from fake_ff1 import DEVICE_ID

P = "ff1_test0001"                                # the fixed entity-id prefix of FF1-TEST0001


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def _setup(hass, ff1) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, data={"host": "127.0.0.1", "port": ff1.port})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _until(check, tries=200):
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("did not happen")


async def test_setup_by_address(hass: HomeAssistant, ff1):
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": "127.0.0.1", "port": ff1.port})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["result"].unique_id == DEVICE_ID and r["data"] == {"host": "127.0.0.1", "port": ff1.port}
    r = await _start(hass)                                          # the same FF1 is not added twice
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": "127.0.0.1", "port": ff1.port})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


@pytest.mark.parametrize(("data", "error"), [({"host": "127.0.0.1", "port": 9}, "cannot_connect"),
                                             ({"host": "http://127.0.0.1/x"}, "invalid_host"),
                                             ({"host": "  "}, "invalid_host")])
async def test_setup_refuses(hass: HomeAssistant, ff1, data, error):
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], data)
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"base": error}


async def test_entities_have_fixed_ids_and_the_device_values(hass: HomeAssistant, ff1):
    await _setup(hass, ff1)
    s = lambda e: hass.states.get(e)                                # noqa: E731
    player = s(f"media_player.{P}_player")
    assert player.state == "playing"
    assert (player.attributes["media_title"], player.attributes["media_artist"]) == ("Study #1", "Ada Example")
    assert player.attributes["entity_picture"] == "https://img.example/1.jpg"
    assert player.attributes["volume_level"] == 0.3 and player.attributes["position"] == 1
    assert s(f"sensor.{P}_now_showing").state == "Study #1"
    assert s(f"sensor.{P}_firmware").state == "3.0.1"
    assert float(s(f"sensor.{P}_cpu_temperature").state) == 55.5
    assert float(s(f"sensor.{P}_disk_used").state) == 60.0
    assert float(s(f"sensor.{P}_wifi_signal").state) == 74
    assert float(s(f"sensor.{P}_cache_used").state) == 50.0
    assert s(f"sensor.{P}_works_needing_attention").state == "0"
    assert s(f"sensor.{P}_outages_24h").state == "2"
    assert s(f"sensor.{P}_last_boot").state == "2026-11-24T09:00:00+00:00"
    for key, state in (("internet", "on"), ("relay", "on"), ("lan_control", "on"), ("sleeping", "off"),
                       ("update_available", "off"), ("media_problem", "off")):
        assert s(f"binary_sensor.{P}_{key}").state == state, key
    assert s(f"number.{P}_panel_brightness").state == "40"
    assert s(f"switch.{P}_sleep_schedule").state == "on"
    assert s(f"button.{P}_reboot") is not None
    # no source URL of a work (they can carry credentials) in any state or attribute
    assert not any("art.example" in str(st.as_dict()) for st in hass.states.async_all())


async def test_controls_reach_the_device(hass: HomeAssistant, ff1):
    await _setup(hass, ff1)
    call = lambda domain, service, entity, **data: hass.services.async_call(   # noqa: E731
        domain, service, {"entity_id": entity, **data}, blocking=True)
    mp = f"media_player.{P}_player"
    await call("media_player", "volume_set", mp, volume_level=0.6)
    assert ff1.volume == 60
    await call("media_player", "volume_mute", mp, is_volume_muted=True)
    assert ff1.muted is True
    await call("media_player", "media_next_track", mp)
    assert ff1.index == 1
    await call("media_player", "turn_off", mp)
    await hass.async_block_till_done()
    assert ff1.sleeping and hass.states.get(mp).state == "off"
    assert hass.states.get(f"binary_sensor.{P}_sleeping").state == "on"
    assert hass.states.get(f"sensor.{P}_now_showing").state == "unknown"      # nothing shows while it sleeps
    await call("media_player", "turn_on", mp)
    assert not ff1.sleeping
    await call("number", "set_value", f"number.{P}_panel_brightness", value=70)
    assert ff1.panel["brightness"] == 70
    await call("switch", "turn_off", f"switch.{P}_sleep_schedule")
    assert ff1.schedule is False
    await call("button", "press", f"button.{P}_reboot")
    assert ff1.rebooted == 1
    sent = {c for c, _ in ff1.calls}
    assert not sent & {"shutdown", "factoryReset", "sshAccess", "updateToLatestVersion", "ff1Envelope"}


async def test_a_refusal_reaches_the_person(hass: HomeAssistant, ff1):
    await _setup(hass, ff1)
    ff1.busy = True
    with pytest.raises(HomeAssistantError, match="Busy"):
        await hass.services.async_call("button", "press", {"entity_id": f"button.{P}_refresh_artwork"}, blocking=True)


async def test_notifications_update_without_a_poll(hass: HomeAssistant, ff1):
    entry = await _setup(hass, ff1)
    await _until(lambda: ff1.sockets)
    ff1.volume, ff1.index = 80, 2
    await ff1.push("device_status", ff1.device())
    await ff1.push("player_status", ff1.player(notification=True))
    mp = f"media_player.{P}_player"
    await _until(lambda: hass.states.get(mp).attributes.get("media_title") == "Study #3")
    assert hass.states.get(mp).attributes["volume_level"] == 0.8
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_broken_work_is_a_problem(hass: HomeAssistant, ff1):
    ff1.broken = [{"index": 1, "title": "Study #2", "state": "broken", "category": "source_missing", "skipped": True}]
    await _setup(hass, ff1)
    assert hass.states.get(f"binary_sensor.{P}_media_problem").state == "on"
    st = hass.states.get(f"sensor.{P}_works_needing_attention")
    assert st.state == "1" and st.attributes["works"] == ["Study #2: broken (source_missing)"]


async def test_lan_closed_keeps_status_and_takes_the_controls_away(hass: HomeAssistant, ff1):
    ff1.lan = "closed"
    entry = await _setup(hass, ff1)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(f"binary_sensor.{P}_lan_control").state == "off"
    assert hass.states.get(f"binary_sensor.{P}_internet").state == "on"
    for e in (f"media_player.{P}_player", f"switch.{P}_sleep_schedule", f"button.{P}_reboot"):
        assert hass.states.get(e).state == "unavailable", e
    assert {c for c, _ in ff1.calls} == set()                        # it did not even try a command


async def test_another_device_at_the_address_is_not_ours(hass: HomeAssistant, ff1):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="FF1-OTHER", data={"host": "127.0.0.1", "port": ff1.port})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_unreachable_device_retries(hass: HomeAssistant, ff1):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, data={"host": "127.0.0.1", "port": 9})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
