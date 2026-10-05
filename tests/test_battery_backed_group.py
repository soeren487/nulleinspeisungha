"""The Battery-backed group: the controller directly, then end to end."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from custom_components.nulleinspeisung.curtailment import (
    ControllableInverter,
    ControlState,
    decide_house,
)
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

# -- the controller, directly ----------------------------------------------


def _inv(
    rated: float = 1000, production: float | None = 1000, limit: float = 100
) -> ControllableInverter:
    return ControllableInverter(rated_power=rated, production=production, limit=limit)


def _house(
    grid: float,
    pv: list[ControllableInverter],
    bb: list[ControllableInverter],
    consumption: float | None = None,
    **kwargs: float,
):
    settings = {"setpoint": 0.0, "band": 30.0, "floor": 5.0, "headroom": 0.0} | kwargs
    return decide_house(
        grid,
        settings["setpoint"],
        settings["band"],
        settings["floor"],
        pv,
        bb,
        consumption,
        settings["headroom"],
    )


def test_lowering_takes_from_the_battery_backed_group_first() -> None:
    """An excess the Battery-backed group can give leaves the PV group alone."""
    decision = _house(-300, [_inv()], [_inv(production=800)])
    assert decision.state is ControlState.LOWERING
    assert decision.battery_backed.allowed_power == pytest.approx(500)
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert not decision.pv.changed


def test_lowering_reaches_the_pv_group_only_at_the_floor() -> None:
    """500 W excess, Battery-backed group can give 300 W: PV gives 200 W."""
    decision = _house(-500, [_inv()], [_inv(production=350)])
    assert decision.battery_backed.allowed_power == pytest.approx(50)  # floor
    assert decision.pv.allowed_power == pytest.approx(800)
    assert decision.pv.changed


def test_an_excess_larger_than_both_groups_ends_at_both_floors() -> None:
    decision = _house(-5000, [_inv()], [_inv(production=400)])
    assert decision.battery_backed.allowed_power == pytest.approx(50)
    assert decision.pv.allowed_power == pytest.approx(50)
    assert decision.state is ControlState.LOWERING


def test_a_silent_battery_backed_group_gives_nothing() -> None:
    """Producing 0 W, the group cannot help: all comes from the PV group."""
    decision = _house(-300, [_inv()], [_inv(production=0, limit=100)])
    assert decision.pv.allowed_power == pytest.approx(700)


def test_raising_restores_pv_before_the_battery_backed_group() -> None:
    """PV at 40 %: 300 W import only raises PV; 900 W also raises the other."""
    pv, bb = [_inv(limit=40)], [_inv(limit=10)]
    small = _house(300, pv, bb)
    assert small.state is ControlState.RAISING
    assert small.pv.allowed_power == pytest.approx(700)
    assert small.battery_backed.allowed_power == pytest.approx(100)
    assert not small.battery_backed.changed
    big = _house(900, pv, bb)
    assert big.pv.allowed_power == pytest.approx(1000)
    assert big.battery_backed.allowed_power == pytest.approx(400)


def test_headroom_raises_pv_but_never_battery_backed() -> None:
    """At the target with 2000 W headroom, only PV rises."""
    decision = _house(0, [_inv(limit=40)], [_inv(limit=10)], headroom=2000)
    assert decision.state is ControlState.RAISING
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert decision.battery_backed.allowed_power == pytest.approx(100)


def test_only_the_grid_part_of_the_deviation_raises_battery_backed() -> None:
    """200 W import with 2000 W headroom: PV has room for all of it."""
    decision = _house(200, [_inv(limit=10)], [_inv(limit=10)], headroom=2000)
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert decision.battery_backed.allowed_power == pytest.approx(100)


def test_pv_serves_the_import_before_headroom_is_left_for_battery_backed() -> None:
    """900 W import, PV can take only 100 W: the other 800 W go on."""
    decision = _house(900, [_inv(limit=90)], [_inv(limit=10)])
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert decision.battery_backed.allowed_power == pytest.approx(900)


def test_at_night_an_import_raises_the_battery_backed_group() -> None:
    decision = _house(250, [], [_inv(limit=10)])
    assert decision.pv is None
    assert decision.state is ControlState.RAISING
    assert decision.battery_backed.allowed_power == pytest.approx(350)


def test_at_night_headroom_alone_does_not_raise() -> None:
    decision = _house(0, [], [_inv(limit=10)], headroom=1500)
    assert decision.battery_backed.allowed_power == pytest.approx(100)


def test_the_cap_lowers_in_a_holding_run() -> None:
    """Inside the band, an allowance of 1000 W with 300 W consumption is cut."""
    decision = _house(0, [_inv()], [_inv()], consumption=300)
    assert decision.state is ControlState.LOWERING
    assert decision.battery_backed.allowed_power == pytest.approx(300)
    assert decision.battery_backed.changed
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert not decision.pv.changed


def test_the_cap_respects_the_band() -> None:
    """Delivering no more than the band above consumption is left alone."""
    decision = _house(0, [], [_inv(limit=33, production=330)], consumption=300)
    assert decision.state is ControlState.HOLDING
    assert decision.battery_backed.allowed_power == pytest.approx(330)
    assert not decision.battery_backed.changed
    over = _house(0, [], [_inv(limit=34, production=340)], consumption=300)
    assert over.battery_backed.allowed_power == pytest.approx(300)


def test_the_cap_respects_the_floor() -> None:
    decision = _house(0, [], [_inv()], consumption=10)
    assert decision.battery_backed.allowed_power == pytest.approx(50)
    decision = _house(0, [], [_inv()], consumption=-40)
    assert decision.battery_backed.allowed_power == pytest.approx(50)


def test_the_cap_is_skipped_when_consumption_is_unknown() -> None:
    decision = _house(0, [_inv()], [_inv()], consumption=None)
    assert decision.state is ControlState.HOLDING
    assert decision.battery_backed.allowed_power == pytest.approx(1000)


def test_a_raised_group_that_does_not_deliver_yet_is_not_capped() -> None:
    """The cap looks at what is delivered, not at the allowance."""
    decision = _house(500, [], [_inv(limit=10, production=100)], consumption=250)
    assert decision.battery_backed.allowed_power == pytest.approx(600)
    assert decision.state is ControlState.RAISING


def test_the_cap_applies_to_a_raise_that_is_delivered() -> None:
    decision = _house(500, [], [_inv(limit=10, production=800)], consumption=250)
    assert decision.battery_backed.allowed_power == pytest.approx(250)


def test_an_unused_allowance_above_consumption_is_left_alone() -> None:
    """A typical night: 300 W delivered, 100 % allowed, load moving."""
    for consumption in (400, 900, 650, 1400):
        decision = _house(0, [], [_inv(rated=1500, production=300)], consumption)
        assert decision.state is ControlState.HOLDING
        assert decision.battery_backed.allowed_power == pytest.approx(1500)
        assert not decision.battery_backed.changed


def test_consumption_below_what_is_delivered_lowers_to_consumption() -> None:
    decision = _house(0, [], [_inv(rated=1500, production=600)], consumption=300)
    assert decision.state is ControlState.LOWERING
    assert decision.battery_backed.allowed_power == pytest.approx(300)


def test_cap_never_raises_the_allowance() -> None:
    """Allowance 200 W, a stale reading of 900 W: consumption 500 W changes nothing."""
    decision = _house(0, [], [_inv(production=900, limit=20)], consumption=500)
    assert decision.battery_backed.allowed_power == pytest.approx(200)


def _released(**kwargs):
    """Allowance 300 W of 1500 W, delivering it, the load at 1000 W."""
    args = {
        "pv": [],
        "bb": [_inv(rated=1500, production=300, limit=20)],
        "consumption": 1000,
    } | kwargs
    return _house(0, args["pv"], args["bb"], args["consumption"])


def test_a_biting_allowance_is_released_to_consumption() -> None:
    """With an AC Battery covering the load the meter shows no import."""
    decision = _released()
    assert decision.state is ControlState.HOLDING
    assert decision.battery_backed.allowed_power == pytest.approx(1000)
    assert decision.battery_backed.changed


def test_release_with_the_pv_group_at_100_percent() -> None:
    decision = _released(pv=[_inv(production=200)])
    assert decision.battery_backed.allowed_power == pytest.approx(1000)
    assert decision.pv.allowed_power == pytest.approx(1000)


def test_no_release_while_the_pv_group_is_below_100_percent() -> None:
    decision = _released(pv=[_inv(limit=60)])
    assert decision.battery_backed.allowed_power == pytest.approx(300)
    assert not decision.battery_backed.changed


def test_no_release_when_the_allowance_is_not_biting() -> None:
    """Delivering 100 W of 300 W allowed: more allowance would not help."""
    decision = _released(bb=[_inv(rated=1500, production=100, limit=20)])
    assert decision.battery_backed.allowed_power == pytest.approx(300)


def test_release_is_capped_at_the_rated_power() -> None:
    decision = _released(consumption=4000)
    assert decision.battery_backed.allowed_power == pytest.approx(1500)


def test_no_release_within_the_band() -> None:
    decision = _released(consumption=325)
    assert decision.battery_backed.allowed_power == pytest.approx(300)


def test_no_release_in_a_run_that_lowers() -> None:
    decision = _house(
        -300,
        [_inv(production=500)],
        [_inv(rated=1500, production=300, limit=20)],
        consumption=1000,
    )
    assert decision.state is ControlState.LOWERING
    assert decision.battery_backed.allowed_power == pytest.approx(75)


def test_unknown_consumption_applies_neither_cap_nor_release() -> None:
    assert _released(consumption=None).battery_backed.allowed_power == pytest.approx(
        300
    )
    capped = _house(0, [], [_inv(production=900)], consumption=None)
    assert capped.battery_backed.allowed_power == pytest.approx(1000)


def _on_its_way(pending: float, **kwargs) -> ControllableInverter:
    return ControllableInverter(
        rated_power=kwargs.get("rated", 1500),
        production=kwargs.get("production", 300),
        limit=kwargs.get("limit", 20),
        pending_change=pending,
    )


def test_the_cap_waits_while_a_change_is_on_its_way() -> None:
    """Delivering 600 W for 300 W of consumption, with 100 W still on its way."""
    bb = [_on_its_way(-100, production=600, limit=40)]
    assert _house(0, [], bb, consumption=300).battery_backed.allowed_power == (
        pytest.approx(600)
    )
    bb = [_on_its_way(-20, production=600, limit=40)]  # within the band
    assert _house(0, [], bb, consumption=300).battery_backed.allowed_power == (
        pytest.approx(300)
    )


def test_the_cap_waits_while_the_pv_group_has_a_change_on_its_way() -> None:
    pv = [_on_its_way(-100, production=500, limit=100)]
    bb = [_inv(rated=1500, production=600, limit=40)]
    assert _house(0, pv, bb, consumption=300).battery_backed.allowed_power == (
        pytest.approx(600)
    )


def test_the_release_waits_while_a_change_is_on_its_way() -> None:
    bb = [_on_its_way(100)]
    assert _released(bb=bb).battery_backed.allowed_power == pytest.approx(300)
    assert not _released(bb=bb).battery_backed.changed


def test_the_cap_does_not_act_on_a_reading_older_than_the_last_command() -> None:
    """Without a reading the group would be taken to deliver its allowance."""
    unknown = [_inv(rated=1500, production=None, limit=100)]
    decision = _house(0, [], unknown, consumption=1000)
    assert decision.battery_backed.allowed_power == pytest.approx(1500)
    assert not decision.battery_backed.changed


def test_the_release_still_assumes_the_allowance_is_biting_without_a_reading() -> None:
    unknown = [_inv(rated=1500, production=None, limit=20)]
    assert _released(bb=unknown).battery_backed.allowed_power == pytest.approx(1000)


def test_the_release_follows_the_curtailed_fact_not_the_pv_allowance() -> None:
    """A ceiling of the response reserve holds the PV group below 100 %."""
    pv = [_inv(limit=60)]
    bb = [_inv(rated=1500, production=300, limit=20)]

    def run(curtailed):
        return decide_house(0, 0.0, 30.0, 5.0, pv, bb, 1000, 0.0, curtailed)

    assert run(False).battery_backed.allowed_power == pytest.approx(1000)
    assert run(True).battery_backed.allowed_power == pytest.approx(300)
    assert run(None).battery_backed.allowed_power == pytest.approx(300)


def _idle(limited: bool | None, limit: float = 30) -> ControllableInverter:
    return ControllableInverter(
        rated_power=1000, production=300, limit=limit, limited=limited
    )


def test_a_raise_reaching_a_group_that_is_not_biting_goes_to_100_percent() -> None:
    decision = _house(100, [], [_idle(False)])
    assert decision.battery_backed.allowed_power == pytest.approx(1000)
    pv = _house(100, [_idle(False)], [])
    assert pv.pv.allowed_power == pytest.approx(1000)


def test_a_raise_reaching_a_biting_or_unread_group_is_the_deviation() -> None:
    assert _house(100, [], [_idle(True)]).battery_backed.allowed_power == (
        pytest.approx(400)
    )
    assert _house(100, [], [_idle(None)]).battery_backed.allowed_power == (
        pytest.approx(400)
    )
    assert _house(
        100, [], [_idle(False), _idle(True)]
    ).battery_backed.allowed_power == (pytest.approx(700))


def test_a_pv_group_that_is_not_biting_still_leaves_the_import_to_the_other() -> None:
    decision = _house(500, [_idle(False, 90)], [_idle(False)])
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert decision.battery_backed.allowed_power == pytest.approx(1000)


def test_a_house_with_only_pv_inverters() -> None:
    decision = _house(-200, [_inv()], [], consumption=0)
    assert decision.battery_backed is None
    assert decision.state is ControlState.LOWERING
    assert decision.pv.allowed_power == pytest.approx(800)
    assert _house(10, [_inv()], []).state is ControlState.HOLDING
    assert _house(200, [_inv(limit=50)], []).state is ControlState.RAISING


def test_a_house_with_only_battery_backed_inverters() -> None:
    lowered = _house(-200, [], [_inv(production=600)], consumption=600)
    assert lowered.pv is None
    assert lowered.state is ControlState.LOWERING
    assert lowered.battery_backed.allowed_power == pytest.approx(400)
    assert _house(10, [], [_inv(limit=50, production=500)], consumption=500).state is (
        ControlState.HOLDING
    )
    assert _house(200, [], [_inv(limit=30, production=300)], consumption=900).state is (
        ControlState.RAISING
    )


def test_no_controllable_inverter() -> None:
    decision = _house(-200, [], [])
    assert decision.state is ControlState.NO_INVERTER
    assert decision.pv is None
    assert decision.battery_backed is None


# -- end to end ------------------------------------------------------------

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
POWER = "system/0/Dc/Battery/Power"


def _dtu(oma: float | None, buero4: float | None) -> SimDtu:
    """OmaOpa (600 W, PV) and Büro 4 (1500 W, Battery-backed); None is unreachable."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (oma, buero4, None), strict=True):
        inverter.reachable = power is not None
        inverter.producing = bool(power)
        inverter.power = power or 0.0
    return dtu


def _entity(hass: HomeAssistant, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"house-home_{key}")


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = _entity(hass, platform, key)
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _grid(hass: HomeAssistant, watts: float) -> None:
    hass.states.async_set(METER, str(watts), {"unit_of_measurement": "W"})


async def _switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch",
        service,
        {"entity_id": _entity(hass, "switch", "curtailment")},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant, freezer, gx: SimGx | None = None) -> None:
    freezer.tick(delta=timedelta(seconds=15))
    meter = hass.states.get(METER)
    hass.states.async_set(METER, meter.state, meter.attributes)
    if gx is not None:
        gx.set(POWER, float(gx.topics[POWER][9:-1]))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _home(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    dtu: SimDtu,
    grid: float,
    gx: SimGx | None = None,
    battery_backed: tuple[str, ...] = (BUERO4,),
    inverters: tuple[str, ...] = (OMA, BUERO4),
) -> None:
    _grid(hass, grid)
    await setup_entry(
        hass,
        dtu_network,
        dtu,
        houses=[
            SimHouse(
                "Home",
                inverters=list(inverters),
                battery_backed=list(battery_backed),
                gx=gx,
            )
        ],
    )


def _limits(dtu_network: DtuNetwork) -> list[tuple[str, int, int]]:
    return sorted(dtu_network.limits())


async def test_by_day_only_the_battery_backed_inverter_is_lowered_first(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """300 W export: Büro 4 (700 W) goes to 400 W, OmaOpa is not touched."""
    await _home(hass, dtu_network, _dtu(300, 700), grid=-300)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(BUERO4, 1, 27)]
    assert _state(hass, "sensor", "battery_backed_limit") == "27"
    assert _state(hass, "sensor", "inverter_limit") == "100"
    assert _state(hass, "sensor", "control_state") == "lowering"


async def test_a_larger_excess_lowers_both_groups(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """800 W export: Büro 4 to its floor, OmaOpa gives the remaining 175 W."""
    await _home(hass, dtu_network, _dtu(300, 700), grid=-800)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(OMA, 1, 21), (BUERO4, 1, 5)]
    assert _state(hass, "sensor", "inverter_limit") == "21"
    assert _state(hass, "sensor", "battery_backed_limit") == "5"


async def test_import_raises_pv_first(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """200 W import raises OmaOpa only; 800 W restores it and then raises Büro 4."""
    await _home(hass, dtu_network, _dtu(300, 700), grid=-800)
    await _switch(hass, "turn_on")
    dtu_network.clear_limits()

    _grid(hass, 200)
    await _tick(hass, freezer)
    assert _limits(dtu_network) == [(OMA, 1, 54)]

    dtu_network.clear_limits()
    _grid(hass, 800)
    await _tick(hass, freezer)
    assert _limits(dtu_network) == [(OMA, 1, 100), (BUERO4, 1, 40)]
    assert _state(hass, "sensor", "control_state") == "raising"


async def test_at_night_without_a_battery_the_load_limits_the_inverter(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """PV unreachable; Büro 4 delivers 800 W, the House consumes 300 W."""
    await _home(hass, dtu_network, _dtu(None, 800), grid=-500)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(BUERO4, 1, 20)]
    assert _state(hass, "sensor", "battery_backed_limit") == "20"
    assert _state(hass, "sensor", "inverter_limit") == "unknown"


async def test_at_night_with_a_battery_the_load_limits_the_inverter(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Büro 4 delivers 500 W, 200 W go into a full battery: the load is 300 W."""
    gx = SimGx()
    gx.charge(level=100.0, power=200.0)
    await _home(hass, dtu_network, _dtu(None, 500), grid=0, gx=gx)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(BUERO4, 1, 20)]
    dtu_network.clear_limits()
    await _tick(hass, freezer, gx)
    assert dtu_network.limits() == []


async def test_a_typical_night_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """300 W delivered, 100 % allowed, the AC Battery covering a moving load."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, _dtu(None, 300), grid=0, gx=gx)
    await _switch(hass, "turn_on")
    for power in (-100.0, -600.0, -200.0, -450.0, -100.0):
        gx.charge(power=power)
        await _tick(hass, freezer, gx)
        assert dtu_network.limits() == []
    assert _state(hass, "sensor", "battery_backed_limit") == "100"


async def test_the_inverter_is_released_when_the_load_comes_back(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Limited to 300 W; the AC Battery then covers a 1000 W load: back up."""
    gx = SimGx()
    gx.charge(level=100.0, power=200.0)
    dtu = _dtu(None, 500)
    await _home(hass, dtu_network, dtu, grid=0, gx=gx)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(BUERO4, 1, 20)]
    dtu_network.clear_limits()

    gx.charge(power=-500.0)  # 500 W delivered + 500 W from the battery
    await _tick(hass, freezer, gx)
    assert _limits(dtu_network) == [(BUERO4, 1, 67)]


async def test_battery_headroom_does_not_raise_the_battery_backed_inverters(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The AC Battery can take 1500 W: OmaOpa rises, Büro 4 stays at its floor."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, _dtu(300, 700), grid=-800, gx=gx)
    await _switch(hass, "turn_on")
    assert _limits(dtu_network) == [(OMA, 1, 21), (BUERO4, 1, 5)]
    dtu_network.clear_limits()

    gx.charge(level=80.0, power=500.0)
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"
    assert _limits(dtu_network) == [(OMA, 1, 100)]


async def test_battery_backed_limit_sensor_only_exists_with_such_inverters(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    await _home(
        hass, dtu_network, _dtu(300, 700), grid=0, battery_backed=(), inverters=(OMA,)
    )
    assert _entity(hass, "sensor", "inverter_limit") is not None
    assert _entity(hass, "sensor", "battery_backed_limit") is None


async def test_switching_curtailment_off_gives_both_groups_100(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    await _home(hass, dtu_network, _dtu(300, 700), grid=-800)
    await _switch(hass, "turn_on")
    dtu_network.clear_limits()
    await _switch(hass, "turn_off")
    assert _limits(dtu_network) == [(OMA, 1, 100), (BUERO4, 1, 100)]
    assert _state(hass, "sensor", "battery_backed_limit") == "unknown"
