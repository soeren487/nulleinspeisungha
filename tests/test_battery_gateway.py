"""The AC battery gateway against a simulated GX, with controlled time."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung import battery_gateway
from custom_components.nulleinspeisung.battery_gateway import (
    BatteryGateway,
    BatteryState,
    GxConnectionError,
    async_probe,
)
from tests.conftest import SimGx, register_gx

KEEPALIVE_NEXT = b'{ "keepalive-options" : ["suppress-republish"] }'


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(delta=timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _start(
    hass: HomeAssistant, gx: SimGx, portal_id: str | None = None
) -> BatteryGateway:
    register_gx(gx)
    gateway = BatteryGateway(hass, gx.host, gx.port, portal_id)
    await gateway.async_start()
    await hass.async_block_till_done()
    return gateway


async def test_portal_id_is_discovered_and_values_are_read(hass: HomeAssistant) -> None:
    """The gateway finds the portal id itself and reads the recorded values."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    assert gateway.portal_id == gx.portal_id
    state = gateway.state
    assert state == BatteryState(
        charge_level=82.0,
        power=-116.0,
        voltage=pytest.approx(53.1, abs=0.01),
        charge_current_limit=100.0,
        grid_setpoint=0.0,
        dynamic_ess_mode=0,
        connected=True,
        fresh=True,
    )
    await gateway.async_stop()


async def test_known_portal_id_skips_discovery(hass: HomeAssistant) -> None:
    """With the portal id from the configuration the serial is not asked for."""
    gx = SimGx()
    gateway = await _start(hass, gx, gx.portal_id)
    assert not any("Serial" in f for f in gx.subscribed_filters)
    assert gateway.state.charge_level == 82.0
    await gateway.async_stop()


async def test_changes_arrive_and_listeners_are_told(hass: HomeAssistant) -> None:
    """A changed value shows in the state and notifies listeners once."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    told: list[BatteryState] = []
    gateway.async_add_listener(lambda: told.append(gateway.state))
    gx.set("system/0/Dc/Battery/Power", 1500)
    gx.set("system/0/Dc/Battery/Soc", 83.0)
    gx.set("settings/0/Settings/CGwacs/AcPowerSetPoint", 50.0)
    gx.set("settings/0/Settings/DynamicEss/Mode", 1)
    gx.set("battery/512/Info/MaxChargeCurrent", 40.0)
    state = gateway.state
    assert (state.power, state.charge_level, state.grid_setpoint) == (1500, 83, 50)
    assert (state.dynamic_ess_mode, state.charge_current_limit) == (1, 40.0)
    assert len(told) == 5
    gx.set("system/0/Dc/Battery/Power", 1500)
    assert len(told) == 5
    await gateway.async_stop()


async def test_null_values_are_unknown(hass: HomeAssistant) -> None:
    """A null value means the value is unknown, not zero."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    for path in (
        "system/0/Dc/Battery/Soc",
        "system/0/Dc/Battery/Power",
        "system/0/Dc/Battery/Voltage",
        "battery/512/Info/MaxChargeCurrent",
        "settings/0/Settings/CGwacs/AcPowerSetPoint",
        "settings/0/Settings/DynamicEss/Mode",
    ):
        gx.set(path, None)
    state = gateway.state
    assert state.charge_level is state.power is state.voltage is None
    assert state.charge_current_limit is state.grid_setpoint is None
    assert state.dynamic_ess_mode is None
    assert state.fresh
    await gateway.async_stop()


async def test_garbage_is_unknown(hass: HomeAssistant) -> None:
    """Payloads that are not a number leave the value unknown."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    gx.topics["system/0/Dc/Battery/Soc"] = "not json"
    gx._deliver("system/0/Dc/Battery/Soc")
    assert gateway.state.charge_level is None
    gx.set("system/0/Dc/Battery/Soc", "full")
    assert gateway.state.charge_level is None
    await gateway.async_stop()


async def test_only_the_needed_topics_are_subscribed(hass: HomeAssistant) -> None:
    """Not the whole tree: the discovery filter and the topics of the state."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    p = gx.portal_id
    assert sorted(gx.subscribed_filters) == sorted(
        [
            "N/+/system/0/Serial",
            f"N/{p}/system/0/Dc/Battery/Soc",
            f"N/{p}/system/0/Dc/Battery/Power",
            f"N/{p}/system/0/Dc/Battery/Voltage",
            f"N/{p}/battery/+/Info/MaxChargeCurrent",
            f"N/{p}/settings/0/Settings/CGwacs/AcPowerSetPoint",
            f"N/{p}/settings/0/Settings/DynamicEss/Mode",
            f"N/{p}/hub4/0/Overrides/Setpoint",
            f"N/{p}/hub4/0/Overrides/MaxDischargePower",
        ]
    )
    assert not any("#" in f for f in gx.subscribed_filters)
    await gateway.async_stop()


async def test_keepalive_first_empty_then_every_30_seconds(
    hass: HomeAssistant, freezer
) -> None:
    """One empty keepalive asks for everything, then the suppress option every 30 s."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    assert gx.keepalives == [b""]
    await _tick(hass, freezer, 29)
    assert gx.keepalives == [b""]
    await _tick(hass, freezer, 1)
    assert gx.keepalives == [b"", KEEPALIVE_NEXT]
    await _tick(hass, freezer, 30)
    await _tick(hass, freezer, 30)
    assert gx.keepalives == [b"", KEEPALIVE_NEXT, KEEPALIVE_NEXT, KEEPALIVE_NEXT]
    assert all(topic == f"R/{gx.portal_id}/keepalive" for topic, _ in gx.published)
    await gateway.async_stop()


async def test_state_goes_stale_60_seconds_after_the_last_value(
    hass: HomeAssistant, freezer
) -> None:
    """Without any value for more than 60 s the state is no longer fresh."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    told: list[bool] = []
    gateway.async_add_listener(lambda: told.append(gateway.state.fresh))
    for _ in range(6):
        await _tick(hass, freezer, 10)
    assert gateway.state.fresh
    assert told == []
    await _tick(hass, freezer, 10)
    assert not gateway.state.fresh
    assert gateway.state.connected
    assert told == [False]
    # A value brings it back at once.
    gx.set("system/0/Dc/Battery/Power", 10)
    assert gateway.state.fresh
    assert told == [False, True]
    await gateway.async_stop()


async def test_state_is_not_fresh_after_a_disconnect(hass: HomeAssistant) -> None:
    """Losing the connection ends freshness at once, but keeps the last values."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    gx.drop()
    state = gateway.state
    assert not state.connected
    assert not state.fresh
    assert state.charge_level == 82.0
    await gateway.async_stop()


async def test_reconnect_subscribes_again_and_asks_for_everything(
    hass: HomeAssistant, freezer
) -> None:
    """After the GX is back the gateway subscribes anew and gets fresh values."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    gx.drop()
    await _tick(hass, freezer, 30)
    # Nothing is published while disconnected.
    assert gx.keepalives == [b""]
    gx.topics["system/0/Dc/Battery/Soc"] = '{"value":70.0}'
    gx.restore()
    await hass.async_block_till_done()
    assert gx.keepalives == [b"", b""]
    assert len(gx.subscribed_filters) == 8  # the serial filter is not needed again
    state = gateway.state
    assert state.connected
    assert state.fresh
    assert state.charge_level == 70.0
    await gateway.async_stop()


async def test_a_gx_that_is_down_at_start_connects_when_it_comes_up(
    hass: HomeAssistant,
) -> None:
    """Start while the GX is away; no error, and the values arrive later."""
    gx = SimGx()
    gx.down = True
    gateway = await _start(hass, gx, gx.portal_id)
    assert not gateway.state.connected
    assert gateway.state.charge_level is None
    gx.restore()
    await hass.async_block_till_done()
    assert gateway.state.fresh
    assert gateway.state.charge_level == 82.0
    await gateway.async_stop()


async def test_only_the_keepalive_is_ever_published(
    hass: HomeAssistant, freezer
) -> None:
    """Nothing is written to the GX."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    gx.set("system/0/Dc/Battery/Soc", 90.0)
    await _tick(hass, freezer, 30)
    gx.drop()
    gx.restore()
    await _tick(hass, freezer, 30)
    assert gx.keepalives
    assert gx.other_publishes == []
    await gateway.async_stop()


async def test_stop_closes_the_connection_and_the_keepalive(
    hass: HomeAssistant, freezer
) -> None:
    """After stopping, the client is closed and nothing is sent any more."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    (client,) = gx.clients or [None]
    assert client is not None
    await gateway.async_stop()
    assert client.closed
    assert gx.clients == []
    sent = len(gx.published)
    await _tick(hass, freezer, 60)
    assert len(gx.published) == sent


async def test_probe_returns_the_portal_id(hass: HomeAssistant) -> None:
    """The probe connects, reads the serial, and disconnects again."""
    gx = register_gx(SimGx())
    assert await async_probe(gx.host, gx.port) == gx.portal_id
    assert gx.clients == []
    assert gx.other_publishes == []


async def test_probe_times_out_for_an_unknown_host(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GX that never answers ends in a connection error after the timeout."""
    monkeypatch.setattr(battery_gateway, "PROBE_TIMEOUT", 0.05)
    with pytest.raises(GxConnectionError):
        await async_probe("nowhere.test", 1883)


async def test_probe_times_out_for_a_gx_that_is_down(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known address that does not connect is the same error."""
    monkeypatch.setattr(battery_gateway, "PROBE_TIMEOUT", 0.05)
    gx = register_gx(SimGx())
    gx.down = True
    with pytest.raises(GxConnectionError):
        await async_probe(gx.host, gx.port)
    assert gx.clients == []


def test_default_probe_timeout_is_about_ten_seconds() -> None:
    """The documented timeout."""
    assert battery_gateway.PROBE_TIMEOUT == 10.0


async def test_a_number_and_null_are_written_to_a_path_of_the_gx(
    hass: HomeAssistant,
) -> None:
    """The write goes to W/<portal id>/<path> as {"value": ...}."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    assert gateway.async_write("hub4/0/Overrides/Setpoint", 50.0)
    assert gateway.async_write("hub4/0/Overrides/Setpoint", None)
    assert gateway.async_write("hub4/0/Overrides/MaxDischargePower", -1)
    p = gx.portal_id
    assert gx.other_publishes == [
        (f"W/{p}/hub4/0/Overrides/Setpoint", b'{"value": 50.0}'),
        (f"W/{p}/hub4/0/Overrides/Setpoint", b'{"value": null}'),
        (f"W/{p}/hub4/0/Overrides/MaxDischargePower", b'{"value": -1}'),
    ]
    await gateway.async_stop()


async def test_nothing_is_written_while_the_gx_is_away(hass: HomeAssistant) -> None:
    """A write without a connection is refused and reported as such."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    gx.drop()
    assert not gateway.async_write("hub4/0/Overrides/Setpoint", None)
    assert gx.other_publishes == []
    await gateway.async_stop()


async def test_the_two_overrides_appear_in_the_state(hass: HomeAssistant) -> None:
    """Unset overrides read as unknown; set ones show their value."""
    gx = SimGx()
    gateway = await _start(hass, gx)
    assert gateway.state.setpoint_override is None
    assert gateway.state.max_discharge_override is None
    gx.set("hub4/0/Overrides/Setpoint", -200)
    gx.set("hub4/0/Overrides/MaxDischargePower", 0)
    assert gateway.state.setpoint_override == -200
    assert gateway.state.max_discharge_override == 0
    gx.set("hub4/0/Overrides/Setpoint", None)
    gx.set("hub4/0/Overrides/MaxDischargePower", None)
    assert gateway.state.setpoint_override is None
    assert gateway.state.max_discharge_override is None
    await gateway.async_stop()
