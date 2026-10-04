"""A House in failure, end to end with simulated DTUs and controlled time."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.nulleinspeisung.const import CONF_NOTIFY_TARGET, DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimHouse, second_dtu, setup_entry
from tests.test_curtailment_flow import (
    BUERO4,
    GARAGE_1,
    GARAGE_2,
    OMA,
    _curtail,
    _dtu,
    _entity,
    _grid,
    _home,
    _state,
    _tick,
)

METER_ISSUE = "grid_meter_failure_house-home"
DTU_ISSUE = "dtu_failure_house-home"


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _entity(hass, "select", "on_failure"), "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _lowered(hass: HomeAssistant, dtu_network: DtuNetwork) -> None:
    """Curtailment on and lowered to 24 % on both Inverters, limits log emptied."""
    await _curtail(hass)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    dtu_network.clear_limits()


async def test_on_failure_select_defaults_to_hold(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The owner sees the choice, hold by default, among the config entities."""
    await _home(hass, dtu_network)
    assert _state(hass, "select", "on_failure") == "hold"
    state = hass.states.get(_entity(hass, "select", "on_failure"))
    assert state is not None
    assert state.attributes["options"] == ["hold", "full"]
    entry = er.async_get(hass).async_get(_entity(hass, "select", "on_failure"))
    assert entry.entity_category == "config"


async def test_unavailable_meter_holds_the_limits_and_raises_an_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """First no Grid Power, after three Update Intervals a failure; nothing is sent."""
    await _home(hass, dtu_network, grid=-500)
    await _lowered(hass, dtu_network)
    _grid(hass, "unavailable")
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "no_grid_power"
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "no_grid_power"
    assert _issue(hass, METER_ISSUE) is None
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "failure"
    assert _issue(hass, METER_ISSUE) is not None
    assert _issue(hass, DTU_ISSUE) is None
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "inverter_limit") == "24"


async def test_recovery_clears_the_issue_and_control_resumes(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A returning meter ends the failure and the loop adjusts again."""
    await _home(hass, dtu_network, grid=-500)
    await _lowered(hass, dtu_network)
    _grid(hass, "unavailable")
    for _ in range(3):
        await _tick(hass, freezer)
    assert _issue(hass, METER_ISSUE) is not None
    _grid(hass, -500)
    await _tick(hass, freezer)
    assert _issue(hass, METER_ISSUE) is None
    assert _state(hass, "sensor", "control_state") == "lowering"
    # Held at 24 % (504 W); 500 W more export goes down to the 5 % floor.
    assert sorted(dtu_network.limits()) == [(OMA, 1, 5), (BUERO4, 1, 5)]


async def test_full_sends_100_once_to_each_reachable_inverter(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """With "full" the Inverters are released once, then nothing more is sent."""
    dtu = _dtu()
    await _home(hass, dtu_network, dtu, grid=-500)
    await _select(hass, "full")
    await _lowered(hass, dtu_network)
    _grid(hass, "unavailable")
    for _ in range(2):
        await _tick(hass, freezer)
    assert dtu_network.limits() == []
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "failure"
    assert sorted(dtu_network.limits()) == [(OMA, 1, 100), (BUERO4, 1, 100)]
    for _ in range(4):
        await _tick(hass, freezer)
    assert len(dtu_network.limits()) == 2
    assert _state(hass, "sensor", "inverter_limit") == "100"
    # Recovery: what was sent is forgotten, so control starts from 100 %.
    dtu_network.clear_limits()
    _grid(hass, -500)
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "lowering"
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]


async def test_full_skips_an_inverter_that_is_not_reachable(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Only reachable Inverters get the 100 %."""
    dtu = _dtu()
    dtu.inverters[1].reachable = False
    await _home(hass, dtu_network, dtu, grid=-500)
    await _select(hass, "full")
    await _curtail(hass)
    dtu_network.clear_limits()
    _grid(hass, "unavailable")
    for _ in range(3):
        await _tick(hass, freezer)
    assert dtu_network.limits() == [(OMA, 1, 100)]


async def test_a_meter_that_stops_reporting_is_detected(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A sensor that keeps its last value but sends nothing new is a failure."""
    await _home(hass, dtu_network, grid=-500)
    await _lowered(hass, dtu_network)
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") != "failure"
    assert _issue(hass, METER_ISSUE) is None
    # Until the failure is found the old value is still acted on.
    dtu_network.clear_limits()
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "failure"
    assert _issue(hass, METER_ISSUE) is not None
    await _tick(hass, freezer)
    assert dtu_network.limits() == []


async def test_a_meter_repeating_the_same_value_is_not_a_failure(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Reporting an unchanged value is still reporting."""
    await _home(hass, dtu_network, grid=-25)
    await _curtail(hass)
    for _ in range(10):
        _grid(hass, -25)
        await _tick(hass, freezer)
        assert _state(hass, "sensor", "control_state") == "holding"
    assert _issue(hass, METER_ISSUE) is None
    assert dtu_network.limits() == []


async def test_one_dtu_down_keeps_control_running_on_the_other(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The House keeps controlling the Inverters it can still reach."""
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
    assert len(dtu_network.limits(one.serial)) == 2
    two.down = True
    dtu_network.apply()
    dtu_network.clear_limits()
    for _ in range(5):
        _grid(hass, -2000)
        await _tick(hass, freezer)
        assert _state(hass, "sensor", "control_state") == "lowering"
    assert _issue(hass, DTU_ISSUE) is None
    assert sorted(s for s, _, _ in dtu_network.limits(one.serial)) == [OMA, BUERO4]
    assert dtu_network.limits(two.serial) == []


async def test_all_dtus_down_is_a_failure(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """When no DTU answers, the House has failed and recovers with the DTU."""
    dtu = _dtu()
    await _home(hass, dtu_network, dtu, grid=-500)
    await _curtail(hass)
    dtu.down = True
    dtu_network.apply()
    dtu_network.clear_limits()
    for _ in range(2):
        _grid(hass, -500)
        await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") != "failure"
    for _ in range(2):
        _grid(hass, -500)
        await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") == "failure"
    assert _issue(hass, DTU_ISSUE) is not None
    assert _issue(hass, METER_ISSUE) is None
    dtu.down = False
    dtu_network.apply()
    _grid(hass, -500)
    await _tick(hass, freezer)
    assert _state(hass, "sensor", "control_state") != "failure"
    assert _issue(hass, DTU_ISSUE) is None


async def test_the_notification_target_is_told_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A failure is sent to the target once, however long it lasts."""
    calls = async_mock_service(hass, "notify", "send_message")
    _grid(hass, -500)
    await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        options={CONF_NOTIFY_TARGET: "notify.owner"},
        houses=[SimHouse("Home", inverters=[OMA, BUERO4])],
    )
    await _curtail(hass)
    _grid(hass, "unavailable")
    for _ in range(8):
        await _tick(hass, freezer)
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.owner"
    assert calls[0].data["message"] == "Grid Meter of House Home delivers no data"


async def test_with_curtailment_off_there_is_no_failure(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Nothing is sent and no issue appears while Curtailment is off."""
    await _home(hass, dtu_network, grid=-500)
    await _select(hass, "full")
    _grid(hass, "unavailable")
    for _ in range(6):
        await _tick(hass, freezer)
    assert dtu_network.limits() == []
    assert _issue(hass, METER_ISSUE) is None
    assert _state(hass, "sensor", "control_state") == "off"


async def test_switching_curtailment_off_clears_the_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The issue belongs to Curtailment and goes away with it."""
    await _home(hass, dtu_network, grid=-500)
    await _curtail(hass)
    _grid(hass, "unavailable")
    for _ in range(3):
        await _tick(hass, freezer)
    assert _issue(hass, METER_ISSUE) is not None
    await _curtail(hass, False)
    assert _issue(hass, METER_ISSUE) is None
    assert _state(hass, "sensor", "control_state") == "off"


async def test_on_failure_choice_survives_a_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The owner's choice is kept across a reload."""
    _grid(hass, 0)
    entry = await setup_entry(
        hass, dtu_network, _dtu(), houses=[SimHouse("Home", inverters=[OMA])]
    )
    await _select(hass, "full")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, "select", "on_failure") == "full"


# -- DTU restart ---------------------------------------------------------------


def _restarted(dtu: SimDtu, dtu_network: DtuNetwork, reachable: bool) -> None:
    """The DTU's uptime dropped and it reports 0 % for every Inverter."""
    dtu.uptime = 10
    for inverter in dtu.inverters:
        inverter.limit = 0.0
        inverter.reachable = reachable
    dtu_network.apply()


async def test_after_a_restart_the_limits_are_sent_again_when_reachable(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Each Inverter gets its limit again, once, when it is reachable, not before."""
    dtu = _dtu()
    dtu.uptime = 5000
    await _home(hass, dtu_network, dtu, grid=-500)
    await _lowered(hass, dtu_network)
    _restarted(dtu, dtu_network, reachable=False)
    for _ in range(2):
        _grid(hass, -25)
        await _tick(hass, freezer)
    assert dtu_network.limits() == []
    dtu.inverters[0].reachable = True
    dtu_network.apply()
    _grid(hass, -25)
    await _tick(hass, freezer)
    assert dtu_network.limits() == [(OMA, 1, 24)]
    dtu.inverters[1].reachable = True
    dtu_network.apply()
    for _ in range(3):
        _grid(hass, -25)
        await _tick(hass, freezer)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]


async def test_after_a_restart_reachable_inverters_are_sent_again_at_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The DTU's reported 0 % is not trusted; the House's own limit goes out."""
    dtu = _dtu()
    dtu.uptime = 5000
    await _home(hass, dtu_network, dtu, grid=-500)
    await _lowered(hass, dtu_network)
    _restarted(dtu, dtu_network, reachable=True)
    for _ in range(3):
        _grid(hass, -25)
        await _tick(hass, freezer)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    assert _state(hass, "sensor", "control_state") == "holding"


async def test_a_restart_by_the_integration_sends_the_limits_again(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Pressing the restart button counts as a restart, too."""
    dtu = _dtu()
    await _home(hass, dtu_network, dtu, grid=-500)
    await _lowered(hass, dtu_network)
    button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{dtu.serial}_restart"
    )
    assert button is not None
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    for _ in range(2):
        _grid(hass, -25)
        await _tick(hass, freezer)
    assert sorted(dtu_network.limits()) == [(OMA, 1, 24), (BUERO4, 1, 24)]


async def test_without_a_restart_nothing_is_sent_again(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A growing uptime changes nothing."""
    dtu = _dtu()
    dtu.uptime = 5000
    await _home(hass, dtu_network, dtu, grid=-500)
    await _lowered(hass, dtu_network)
    for uptime in (5010, 5020, 5030):
        dtu.uptime = uptime
        dtu_network.apply()
        _grid(hass, -25)
        await _tick(hass, freezer)
    assert dtu_network.limits() == []
