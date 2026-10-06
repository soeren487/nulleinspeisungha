"""The House switches: on for a new House, as left for an existing one."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
TOPIC = "grid/test/house"
CURTAILMENT = "switch.home_curtailment"
PUBLISH = "switch.home_publish_grid_power"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _dtu() -> SimDtu:
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (300.0, 700.0, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


def _grid(hass: HomeAssistant, watts: float) -> None:
    hass.states.async_set(METER, str(watts), {"unit_of_measurement": "W"})


def _switch(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _sent(mqtt_mock: MagicMock) -> list[tuple]:
    return [c.args for c in mqtt_mock.async_publish.call_args_list]


async def _tick(hass: HomeAssistant, freezer, seconds: float = 15) -> None:
    freezer.tick(delta=timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    gx: SimGx,
    stored: dict[str, str | None],
):
    _grid(hass, -500)
    gx.topics["hub4/0/Overrides/Setpoint"] = '{"value": -150}'
    return await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                gx=gx,
                grid_topic=TOPIC,
                stored_switches=stored,
            )
        ],
    )


async def test_a_new_house_has_both_switches_on_and_works_unasked(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """Nothing stored: Curtailment runs, Grid Power is published, overrides go."""
    gx = SimGx()
    none = {"curtailment": None, "publish_grid_power": None}
    await _setup(hass, dtu_network, gx, none)
    assert _switch(hass, CURTAILMENT) == "on"
    assert _switch(hass, PUBLISH) == "on"
    # 500 W export against a setpoint of 0 W: the limits are lowered by themselves.
    await _tick(hass, freezer)
    assert dtu_network.limits() != []
    assert (
        f"W/{gx.portal_id}/hub4/0/Overrides/Setpoint",
        b'{"value": null}',
    ) in gx.other_publishes
    _grid(hass, -400)
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, '{"grid": {"power": -400.0}}', 0, False)]
    dtu_network.clear_limits()
    _grid(hass, -100)
    await _tick(hass, freezer)
    assert dtu_network.limits() != []


async def test_the_entity_ids_did_not_change(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Unique ids and entity ids are those of the old names."""
    none = {"curtailment": None, "publish_grid_power": None}
    await _setup(hass, dtu_network, SimGx(), none)
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("switch", DOMAIN, "house-home_curtailment")
        == CURTAILMENT
    )
    assert (
        registry.async_get_entity_id("switch", DOMAIN, "house-home_publish_grid_power")
        == PUBLISH
    )


async def test_a_stored_off_stays_off_and_nothing_goes_out(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """Stored off survives a restart; no limit, message or GX write at any time."""
    gx = SimGx()
    off = {"curtailment": "off", "publish_grid_power": "off"}
    entry = await _setup(hass, dtu_network, gx, off)

    async def assert_quiet() -> None:
        assert _switch(hass, CURTAILMENT) == "off"
        assert _switch(hass, PUBLISH) == "off"
        assert dtu_network.limits() == []
        assert _sent(mqtt_mock) == []
        assert gx.other_publishes == []

    await assert_quiet()
    _grid(hass, -300)
    await _tick(hass, freezer)
    await assert_quiet()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await assert_quiet()
    _grid(hass, -200)
    await _tick(hass, freezer)
    await assert_quiet()


async def test_each_switch_keeps_its_own_stored_state(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Curtailment stored off with publishing on, and the other way round."""
    gx = SimGx()
    entry = await _setup(
        hass, dtu_network, gx, {"curtailment": "off", "publish_grid_power": "on"}
    )
    assert _switch(hass, CURTAILMENT) == "off"
    assert _switch(hass, PUBLISH) == "on"
    assert dtu_network.limits() == []
    assert gx.other_publishes != []  # publishing is on: the override is released
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_publishing_stored_off_with_curtailment_stored_on(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """Curtailment runs; publishing stays off and writes nothing."""
    gx = SimGx()
    await _setup(
        hass, dtu_network, gx, {"curtailment": "on", "publish_grid_power": "off"}
    )
    assert _switch(hass, CURTAILMENT) == "on"
    assert _switch(hass, PUBLISH) == "off"
    await _tick(hass, freezer)
    assert dtu_network.limits() != []
    _grid(hass, -100)
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == []
    assert gx.other_publishes == []


async def test_a_stored_on_stays_on_after_a_restart(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Both switches stored on are on again after a reload."""
    on = {"curtailment": "on", "publish_grid_power": "on"}
    entry = await _setup(hass, dtu_network, SimGx(), on)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _switch(hass, CURTAILMENT) == "on"
    assert _switch(hass, PUBLISH) == "on"
    _grid(hass, -50)
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, '{"grid": {"power": -50.0}}', 0, False)]


async def test_owner_off_survives_a_restart_of_a_new_house(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """A new House starts on; what the owner then switches off stays off."""
    none = {"curtailment": None, "publish_grid_power": None}
    entry = await _setup(hass, dtu_network, SimGx(), none)
    for entity_id in (CURTAILMENT, PUBLISH):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    dtu_network.clear_limits()
    mqtt_mock.async_publish.reset_mock()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _switch(hass, CURTAILMENT) == "off"
    assert _switch(hass, PUBLISH) == "off"
    _grid(hass, -50)
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == []


async def test_the_second_switch_needs_a_battery_and_a_topic(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House without a topic has Curtailment, on, but no publishing switch."""
    _grid(hass, 0)
    none = {"curtailment": None, "publish_grid_power": None}
    await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse("Home", inverters=[OMA, BUERO4], gx=SimGx(), stored_switches=none)
        ],
    )
    assert _switch(hass, CURTAILMENT) == "on"
    assert hass.states.get(PUBLISH) is None
