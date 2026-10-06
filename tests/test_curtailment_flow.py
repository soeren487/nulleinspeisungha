"""Curtailment of a House, end to end with simulated DTUs and controlled time."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimHouse, second_dtu, setup_entry

OMA, BUERO4, BUERO5 = "114183178036", "116191100801", "1164a00ccd81"
GARAGE_1, GARAGE_2 = "200000000001", "200000000002"
METER = "sensor.grid_meter"


def _dtu() -> SimDtu:
    """The default DTU with all Inverters reachable: 600 W, 1500 W, 1600 W rated."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (300.0, 700.0, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


def _entity(hass: HomeAssistant, platform: str, key: str, house: str = "home") -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"house-{house}_{key}"
    )
    assert entity_id is not None, f"no {platform} {house} {key}"
    return entity_id


def _state(hass: HomeAssistant, platform: str, key: str, house: str = "home") -> str:
    state = hass.states.get(_entity(hass, platform, key, house))
    assert state is not None
    return state.state


def _grid(hass: HomeAssistant, watts: float | str, meter: str = METER) -> None:
    hass.states.async_set(meter, str(watts), {"unit_of_measurement": "W"})


async def _call(
    hass: HomeAssistant,
    domain: str,
    service: str,
    key: str,
    house: str = "home",
    **data,
) -> None:
    await hass.services.async_call(
        domain,
        service,
        {"entity_id": _entity(hass, domain, key, house), **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _curtail(hass: HomeAssistant, on: bool = True, house: str = "home") -> None:
    await _call(hass, "switch", "turn_on" if on else "turn_off", "curtailment", house)


async def _set(
    hass: HomeAssistant, key: str, value: float, house: str = "home"
) -> None:
    await _call(hass, "number", "set_value", key, house, value=value)


async def _tick(hass: HomeAssistant, freezer, seconds: float = 15) -> None:
    freezer.tick(delta=timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _only_relative_non_persistent(dtu_network: DtuNetwork) -> None:
    for dtu in dtu_network.dtus.values():
        assert {limit_type for _, limit_type, _ in dtu_network.limits(dtu.serial)} <= {
            1
        }


async def _home(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    *dtus: SimDtu,
    inverters: list[str] | None = None,
    grid: float | str | None = 0,
) -> None:
    if grid is not None:
        _grid(hass, grid)
    await setup_entry(
        hass,
        dtu_network,
        *(dtus or (_dtu(),)),
        houses=[SimHouse("Home", inverters=inverters or [OMA, BUERO4])],
    )


async def test_defaults_and_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House with Curtailment stored off has the documented defaults."""
    await _home(hass, dtu_network)
    assert _state(hass, "switch", "curtailment") == "off"
    assert float(_state(hass, "number", "feed_in_setpoint")) == 0
    assert float(_state(hass, "number", "update_interval")) == 15
    assert float(_state(hass, "number", "tolerance_band")) == 30
    assert float(_state(hass, "number", "limit_floor")) == 5
    assert _state(hass, "sensor", "control_state") == "off"
    assert _state(hass, "sensor", "inverter_limit") == "unknown"
    registry = er.async_get(hass)
    for key, category in (
        ("update_interval", "config"),
        ("tolerance_band", "config"),
        ("limit_floor", "config"),
        ("feed_in_setpoint", None),
    ):
        entry = registry.async_get(_entity(hass, "number", key))
        assert entry.entity_category == category


async def test_curtailment_off_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """While Curtailment is off nothing is ever sent, whatever the Grid Power."""
    await _home(hass, dtu_network, grid=-800)
    for _ in range(3):
        await _tick(hass, freezer)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "off"


async def test_export_lowers_every_reachable_inverter_equally(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Export beyond setpoint plus band gives all Inverters the same percent."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    # allowed 2100 W, produced 1000 W: 1000 - 500 = 500 W of 2100 W = 24 %
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    assert _state(hass, "sensor", "control_state") == "lowering"
    assert _state(hass, "sensor", "inverter_limit") == "24"
    _only_relative_non_persistent(dtu_network)


async def test_commands_carry_the_admin_login(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Limits are sent with the DTU's administrator login."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    posts = dtu_network.posts("199980126212", "/api/limit/config")
    assert posts
    assert all(h["Authorization"].startswith("Basic ") for _, h in posts)


async def test_inside_the_band_nothing_is_sent(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A Grid Power within the band of the target holds the limits."""
    await _home(hass, dtu_network, grid=-25)
    await _curtail(hass)
    await _tick(hass, freezer)
    _grid(hass, 30)
    await _tick(hass, freezer)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "holding"


async def test_unchanged_percent_is_not_sent_again(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Only a changed limit is sent."""
    await _home(hass, dtu_network, grid=-5000)
    await _curtail(hass)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 5), (BUERO4, 1, 5)]
    # Still far too much export, but the floor holds: the same 5 % again.
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 2
    # Inside the band the limits are held as well.
    _grid(hass, 10)
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 2
    # A different percent goes out: 105 W allowed + 400 W = 505 W = 24 %.
    _grid(hass, 400)
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 4
    assert {value for _, _, value in dtu_network.limits()[2:]} == {24}


async def test_import_raises_the_limits_back_to_100(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Importing power raises the limits step by step, up to 100 %."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    dtu_network.clear_limits()
    _grid(hass, 400)
    await _tick(hass, freezer)
    # raise from the allowance of 504 W by 400 W = 904 W = 43 %
    assert sorted(dtu_network.limits()) == [(OMA, 1, 43), (BUERO4, 1, 43)]
    assert _state(hass, "sensor", "control_state") == "raising"
    dtu_network.clear_limits()
    _grid(hass, 5000)
    await _tick(hass, freezer)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 100), (BUERO4, 1, 100)]
    assert _state(hass, "sensor", "inverter_limit") == "100"


async def test_export_below_setpoint_minus_band_raises(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """With a setpoint of 300 W export, 100 W export is too little."""
    await _home(hass, dtu_network, grid=-500)
    await _set(hass, "feed_in_setpoint", 300)
    await _curtail(hass)
    # target -300 W; deviation -200 W; 1000 W - 200 W = 800 W of 2100 W = 38 %
    assert sorted(dtu_network.limits()) == [(OMA, 1, 38), (BUERO4, 1, 38)]
    dtu_network.clear_limits()
    _grid(hass, -100)
    await _tick(hass, freezer)
    # deviation +200 W: from the allowance of 798 W -> 998 W = 48 %
    assert sorted(dtu_network.limits()) == [(OMA, 1, 48), (BUERO4, 1, 48)]


async def test_tolerance_band_and_floor_are_settings(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A wide band holds; a high floor stops the lowering."""
    await _home(hass, dtu_network, grid=-100)
    await _set(hass, "tolerance_band", 150)
    await _curtail(hass)
    assert dtu_network.limits() == []
    await _set(hass, "tolerance_band", 30)
    await _set(hass, "limit_floor", 40)
    _grid(hass, -5000)
    await _tick(hass, freezer)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 40), (BUERO4, 1, 40)]


async def test_unreachable_inverter_gets_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """An Inverter that is not reachable is neither counted nor commanded."""
    dtu = _dtu()
    dtu.inverters[1].reachable = False
    await _home(hass, dtu_network, dtu, grid=-300)
    await _curtail(hass)
    # Only the 600 W Inverter counts: 300 W - 300 W = 0 W, lifted to the floor.
    assert dtu_network.limits() == [(OMA, 1, 5)]
    _grid(hass, 0)
    await _tick(hass, freezer)
    assert all(serial == OMA for serial, _, _ in dtu_network.limits())


async def test_inverter_without_rated_power_is_left_alone(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """An Inverter whose rated power is unknown cannot be controlled."""
    dtu = _dtu()
    dtu.inverters[0].devinfo_valid = False
    dtu.inverters[0].rated_power = None
    await _home(hass, dtu_network, dtu, grid=-300)
    await _curtail(hass)
    assert {serial for serial, _, _ in dtu_network.limits()} == {BUERO4}


async def test_no_controllable_inverter(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """With nothing reachable the control says so and sends nothing."""
    dtu = _dtu()
    for inverter in dtu.inverters:
        inverter.reachable = False
    await _home(hass, dtu_network, dtu, grid=-300)
    await _curtail(hass)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "no_inverter"
    assert _state(hass, "sensor", "inverter_limit") == "unknown"


async def test_inverters_on_two_dtus_both_get_limits(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The group spans DTUs; every DTU gets its commands, with the same percent."""
    one, two = _dtu(), second_dtu()
    for inverter in two.inverters:
        inverter.reachable = True
        inverter.power = 100.0
    await _home(
        hass,
        dtu_network,
        one,
        two,
        inverters=[OMA, BUERO4, GARAGE_1, GARAGE_2],
        grid=-600,
    )
    await _curtail(hass)
    first, second = dtu_network.limits(one.serial), dtu_network.limits(two.serial)
    assert sorted(s for s, _, _ in first) == [OMA, BUERO4]
    assert sorted(s for s, _, _ in second) == [GARAGE_1, GARAGE_2]
    # allowed 4200 W, produced 1200 W: 600 W of 4200 W = 14 %
    assert {value for _, _, value in first + second} == {14}
    _only_relative_non_persistent(dtu_network)


async def test_other_inverters_never_get_a_limit(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Unassigned Inverters and those of another House are left alone."""
    _grid(hass, -400)
    _grid(hass, -400, "sensor.other_meter")
    await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse("Home", inverters=[OMA]),
            SimHouse("Other", grid_meter="sensor.other_meter", inverters=[BUERO4]),
        ],
    )
    await _curtail(hass)
    await _tick(hass, freezer)
    assert {serial for serial, _, _ in dtu_network.limits()} == {OMA}
    dtu_network.clear_limits()
    await _curtail(hass, house="other")
    assert {serial for serial, _, _ in dtu_network.limits()} == {BUERO4}
    # BUERO5 belongs to no House and never gets anything.
    await _tick(hass, freezer)
    assert BUERO5 not in {serial for serial, _, _ in dtu_network.limits()}


async def test_switching_off_sends_100_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Switching Curtailment off returns every reachable Inverter to 100 %."""
    dtu = _dtu()
    dtu.inverters[2].reachable = False
    await _home(hass, dtu_network, dtu, inverters=[OMA, BUERO4, BUERO5], grid=-500)
    await _curtail(hass)
    dtu_network.clear_limits()
    await _curtail(hass, False)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 100), (BUERO4, 1, 100)]
    assert _state(hass, "sensor", "control_state") == "off"
    assert _state(hass, "sensor", "inverter_limit") == "unknown"
    for _ in range(3):
        await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 2


async def test_limits_are_resent_after_off_and_on(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """What was sent is forgotten on switch-off, so the same percent goes out again."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    await _curtail(hass, False)
    dtu_network.clear_limits()
    await _curtail(hass)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]


async def test_update_interval_sets_the_cadence(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The loop runs every Update Interval and follows a change without reload."""
    await _home(hass, dtu_network, grid=0)
    await _curtail(hass)
    _grid(hass, -500)
    await _tick(hass, freezer, 14)
    assert dtu_network.limits() == []
    await _tick(hass, freezer, 1)
    assert len(dtu_network.limits()) == 2
    dtu_network.clear_limits()

    await _set(hass, "update_interval", 40)
    _grid(hass, -100)
    await _tick(hass, freezer, 39)
    assert dtu_network.limits() == []
    await _tick(hass, freezer, 1)
    assert len(dtu_network.limits()) == 2
    dtu_network.clear_limits()

    await _set(hass, "update_interval", 5)
    _grid(hass, -100)
    await _tick(hass, freezer, 5)
    assert len(dtu_network.limits()) == 2


async def test_unknown_grid_power_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Without a Grid Power the run does nothing and says so."""
    await _home(hass, dtu_network, grid=None)
    await _curtail(hass)
    assert _state(hass, "sensor", "control_state") == "no_grid_power"
    for bad in ("unavailable", "unknown"):
        _grid(hass, bad)
        await _tick(hass, freezer)
        assert _state(hass, "sensor", "control_state") == "no_grid_power"
    assert dtu_network.limits() == []
    _grid(hass, -500)
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 2
    assert _state(hass, "sensor", "control_state") == "lowering"


async def test_failed_send_is_retried_on_the_next_run(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A refused limit is not remembered as sent."""
    dtu = _dtu()
    dtu.limit_fails = True
    await _home(hass, dtu_network, dtu, grid=-500)
    await _curtail(hass)
    assert len(dtu_network.limits()) == 2
    dtu.limit_fails = False
    dtu_network.apply()
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 4
    assert {value for _, _, value in dtu_network.limits()} == {24}
    _grid(hass, -10)
    await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 4


async def test_dtu_that_does_not_answer_gets_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """While a DTU is down its Inverters are not controllable."""
    dtu = _dtu()
    await _home(hass, dtu_network, dtu, grid=-500)
    dtu.down = True
    dtu_network.apply()
    await _tick(hass, freezer, 15)
    await _curtail(hass)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "no_inverter"


async def test_reported_limit_is_not_trusted(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A DTU that reports 0 % everywhere (as after a restart) does not matter."""
    dtu = _dtu()
    for inverter in dtu.inverters:
        inverter.limit = 0.0
    await _home(hass, dtu_network, dtu, grid=-500)
    await _curtail(hass)
    # Derived from the real production of 1000 W, not the floor: 500 W = 24 %.
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]


async def test_import_after_lowering_does_not_jump_above_the_allowance(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The DTU still reports the old production; raising goes from the allowance."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    dtu_network.clear_limits()
    # The old, higher production (1000 W) and a data age of over an hour remain.
    _grid(hass, 100)
    await _tick(hass, freezer)
    # 504 W allowed + 100 W = 604 W = 29 %, not 1000 W + 100 W = 52 %.
    assert sorted(dtu_network.limits()) == [(OMA, 1, 29), (BUERO4, 1, 29)]


async def test_inverter_that_returns_is_taken_to_be_at_100(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """An Inverter that was unreachable has lost its non-persistent limit."""
    dtu = _dtu()
    entry = await setup_entry(
        hass, dtu_network, dtu, houses=[SimHouse("Home", inverters=[OMA, BUERO4])]
    )
    _grid(hass, -500)
    await _curtail(hass)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    coordinator = next(iter(entry.runtime_data.dtus.values()))
    control = next(iter(entry.runtime_data.houses.values())).control

    dtu.inverters[1].reachable = False
    dtu_network.apply()
    await coordinator.async_refresh()
    _grid(hass, -10)
    await control.async_step()

    dtu.inverters[1].reachable = True
    dtu_network.apply()
    await coordinator.async_refresh()
    dtu_network.clear_limits()
    _grid(hass, 400)
    await control.async_step()
    # OMA 24 % of 600 W = 144 W, BUERO4 100 % of 1500 W: 1644 W + 400 W = 97 %.
    assert sorted(dtu_network.limits()) == [(OMA, 1, 97), (BUERO4, 1, 97)]


async def test_settings_survive_a_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Switch and numbers come back, and the loop uses them from its first run."""
    _grid(hass, -500)
    entry = await setup_entry(
        hass, dtu_network, _dtu(), houses=[SimHouse("Home", inverters=[OMA, BUERO4])]
    )
    await _set(hass, "feed_in_setpoint", 200)
    await _set(hass, "update_interval", 20)
    await _set(hass, "tolerance_band", 50)
    await _set(hass, "limit_floor", 10)
    await _curtail(hass)
    # target -200 W; deviation -300 W; 1000 W - 300 W = 700 W of 2100 W = 33 %
    assert sorted(dtu_network.limits()) == [(OMA, 1, 33), (BUERO4, 1, 33)]
    dtu_network.clear_limits()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, "switch", "curtailment") == "on"
    assert float(_state(hass, "number", "feed_in_setpoint")) == 200
    assert float(_state(hass, "number", "update_interval")) == 20
    assert float(_state(hass, "number", "tolerance_band")) == 50
    assert float(_state(hass, "number", "limit_floor")) == 10
    # A reload sends nothing by itself; the first run after it starts afresh.
    dtu_network.clear_limits()
    _grid(hass, -5000)
    await _tick(hass, freezer, 19)
    assert dtu_network.limits() == []
    await _tick(hass, freezer, 1)
    # Floor 10 % holds, so 10 % goes out to both.
    assert sorted(dtu_network.limits()) == [(OMA, 1, 10), (BUERO4, 1, 10)]
    _only_relative_non_persistent(dtu_network)


async def test_removing_the_loop_on_unload_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Unloading the entry neither sends 100 % nor leaves a running loop."""
    _grid(hass, -500)
    entry = await setup_entry(
        hass, dtu_network, _dtu(), houses=[SimHouse("Home", inverters=[OMA, BUERO4])]
    )
    await _curtail(hass)
    dtu_network.clear_limits()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert dtu_network.limits() == []
