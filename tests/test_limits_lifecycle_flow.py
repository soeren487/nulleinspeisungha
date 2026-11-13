"""The limits of the Inverters when the House goes away and when it comes back."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
POWER = "system/0/Dc/Battery/Power"


def _dtu(limit: float = 100.0, watts: tuple[float, float] = (300.0, 700.0)) -> SimDtu:
    """600 W and 1500 W Inverters, at ``limit`` percent."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (*watts, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
        inverter.limit = limit
    return dtu


def _grid(hass: HomeAssistant, watts: float) -> None:
    hass.states.async_set(METER, str(watts), {"unit_of_measurement": "W"})


async def _tick(hass: HomeAssistant, freezer, gx: SimGx | None, seconds=15) -> None:
    freezer.tick(delta=timedelta(seconds=seconds))
    meter = hass.states.get(METER)
    if meter is not None:
        hass.states.async_set(METER, meter.state, meter.attributes)
    if gx is not None and not gx.down:
        gx.set(POWER, float(gx.topics[POWER][9:-1]))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _curtailing_house(hass, dtu_network, dtu, gx=None, grid=-500):
    """A House with Curtailment on that has limited both Inverters."""
    _grid(hass, grid)
    entry = await setup_entry(
        hass,
        dtu_network,
        dtu,
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                gx=gx,
                stored_switches={"curtailment": "on", "publish_grid_power": "off"},
            )
        ],
    )
    await hass.async_block_till_done()
    return entry


def _last(dtu_network: DtuNetwork) -> dict[str, int]:
    return {serial: value for serial, _type, value in dtu_network.limits()}


async def test_limits_the_house_left_before_a_restart_are_raised_again(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The Inverters are at 10 % from before Home Assistant restarted; the AC
    Battery is full and covers the load. The House used to assume 100 % and never
    raise them while the battery drained."""
    gx = SimGx()
    gx.charge(level=100.0, power=-300.0)
    await _curtailing_house(
        hass, dtu_network, _dtu(limit=10.0, watts=(60.0, 150.0)), gx, grid=0
    )
    for _ in range(8):
        await _tick(hass, freezer, gx)

    limits = _last(dtu_network)
    assert limits, "the House raised nothing"
    assert limits[OMA] > 10
    assert limits[BUERO4] > 10


async def test_a_limit_of_zero_is_not_taken_over(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A DTU that restarted reports 0 %: that is not a limit of the House's."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    entry = await _curtailing_house(hass, dtu_network, _dtu(limit=0.0), gx, grid=-500)
    await _tick(hass, freezer, gx)
    house = next(iter(entry.runtime_data.houses.values()))
    assert house.control.limited_serials <= {OMA, BUERO4}
    assert all(
        percent >= 5 for percent in house.control._sent.values()
    )  # only what it sent itself


async def test_disabling_the_entry_gives_the_inverters_100_percent(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    entry = await _curtailing_house(hass, dtu_network, _dtu())
    await _tick(hass, freezer, None)
    assert max(_last(dtu_network).values()) < 100

    await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert _last(dtu_network) == {OMA: 100, BUERO4: 100}


async def test_a_reload_leaves_the_limits_as_they_are(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    entry = await _curtailing_house(hass, dtu_network, _dtu())
    await _tick(hass, freezer, None)
    assert max(_last(dtu_network).values()) < 100
    dtu_network.clear_limits()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert 100 not in _last(dtu_network).values()


async def test_removing_the_entry_gives_the_inverters_100_percent(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    entry = await _curtailing_house(hass, dtu_network, _dtu())
    await _tick(hass, freezer, None)
    assert max(_last(dtu_network).values()) < 100

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _last(dtu_network) == {OMA: 100, BUERO4: 100}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_removing_an_entry_that_limited_nothing_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    _grid(hass, 0)
    entry = await setup_entry(
        hass,
        dtu_network,
        _dtu(limit=60.0),
        houses=[SimHouse("Home", inverters=[OMA, BUERO4])],
    )
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert dtu_network.limits() == []


async def test_home_assistant_stopping_with_hold_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    await _curtailing_house(hass, dtu_network, _dtu())
    await _tick(hass, freezer, None)
    dtu_network.clear_limits()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert dtu_network.limits() == []


async def test_home_assistant_stopping_with_100_percent_releases_the_limits(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    entry = await _curtailing_house(hass, dtu_network, _dtu())
    await _tick(hass, freezer, None)
    assert max(_last(dtu_network).values()) < 100
    next(iter(entry.runtime_data.houses.values())).control.on_failure = "full"

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert _last(dtu_network) == {OMA: 100, BUERO4: 100}


async def test_a_dtu_that_refuses_the_limit_does_not_hold_up_the_shutdown(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    dtu = _dtu()
    entry = await _curtailing_house(hass, dtu_network, dtu)
    await _tick(hass, freezer, None)
    dtu.limit_fails = True
    next(iter(entry.runtime_data.houses.values())).control.on_failure = "full"

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
