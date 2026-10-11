"""The back-off of the probe, with instant Inverters and controlled time."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.nulleinspeisung.house_control import MAXIMUM_BACKOFF
from tests.conftest import DtuNetwork, SimGx
from tests.test_battery_curtailment_flow import (
    BUERO4,
    OMA,
    _curtail,
    _grid,
    _home,
    _limits,
    _state,
    _tick,
    _warm,
)
from tests.test_ramp_flow import _control


async def _until_probing(hass: HomeAssistant, freezer, gx, limit: int = 60) -> int:
    """Runs of 15 s with the meter at 0 until a probe; how many it took."""
    _grid(hass, 0)
    for runs in range(1, limit + 1):
        await _tick(hass, freezer, gx)
        if _state(hass, "sensor", "control_state") == "probing":
            return runs
    raise AssertionError("no probe")


async def test_without_a_battery_a_probe_taken_back_waits_and_doubles(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Probe, export, lowering: wait 60 s; again: 120, 240, 480, then 600 at most."""
    await _home(hass, dtu_network, None, grid=-500)
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    control = _control(hass)
    assert control.probe_backoff == 0

    # At the target the House probes: one step of 3 % of 2100 W (63 W).
    dtu_network.clear_limits()
    assert await _until_probing(hass, freezer, None) == 1
    assert _limits(dtu_network) == [(OMA, 1, 27), (BUERO4, 1, 27)]
    assert control.probe_backoff == 0

    waits = []
    for expected in (60, 120, 240, 480, 600, 600):
        # The probe shows as export: lowered, and the next probe is held back.
        _grid(hass, -100)
        await _tick(hass, freezer, None)
        assert _state(hass, "sensor", "control_state") == "lowering"
        assert control.probe_backoff == expected
        waits.append(await _until_probing(hass, freezer, None))
    # 60 s is four runs of 15 s; each wait is as long as the back-off.
    assert waits == [4, 8, 16, 32, 40, 40]
    assert MAXIMUM_BACKOFF == 600


async def test_an_import_resets_the_back_off(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    await _home(hass, dtu_network, None, grid=-500)
    await _curtail(hass)
    await _until_probing(hass, freezer, None)
    _grid(hass, -100)
    await _tick(hass, freezer, None)
    control = _control(hass)
    assert control.probe_backoff == 60
    # An import is a reason to raise that is never held back. One import run
    # does not end the wait (a noisy meter), a second in a row does.
    _grid(hass, 120)
    await _tick(hass, freezer, None)
    assert _state(hass, "sensor", "control_state") == "raising"
    assert control.probe_backoff == 60
    _grid(hass, 250)
    await _tick(hass, freezer, None)
    assert control.probe_backoff == 0
    # ... so the next probe is not delayed.
    assert await _until_probing(hass, freezer, None) == 1


async def test_a_raise_for_an_import_is_never_held_back_by_the_back_off(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    await _home(hass, dtu_network, None, grid=-500)
    await _curtail(hass)
    await _until_probing(hass, freezer, None)
    _grid(hass, -100)
    await _tick(hass, freezer, None)
    assert _control(hass).probe_backoff == 60
    dtu_network.clear_limits()
    _grid(hass, 100)
    await _tick(hass, freezer, None)
    assert dtu_network.limits() != []
    assert _state(hass, "sensor", "control_state") == "raising"


async def test_headroom_appearing_resets_the_back_off(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A full battery makes the House wait; one that gets room ends the wait."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _warm(hass, freezer, gx)
    await _curtail(hass)
    await _tick(hass, freezer, gx)  # the second run in a row lowers
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    _grid(hass, 0)
    await _tick(hass, freezer, gx, 60)  # the mean of the last minute is 0
    assert _state(hass, "sensor", "control_state") == "probing"
    _grid(hass, -300)
    await _tick(hass, freezer, gx, 60)
    assert _state(hass, "sensor", "control_state") == "holding"  # export, first run
    await _tick(hass, freezer, gx, 60)
    assert _state(hass, "sensor", "control_state") == "lowering"
    control = _control(hass)
    assert control.probe_backoff == 60

    # The battery is not full any more: 1900 W of room (2100 - 100 W margin - 100).
    gx.charge(level=99.0, power=100.0)
    _grid(hass, 0)
    await _tick(hass, freezer, gx, 60)
    assert _state(hass, "sensor", "control_state") == "raising"
    assert control.probe_backoff == 0


async def test_switching_curtailment_off_and_on_resets_the_back_off(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    await _home(hass, dtu_network, None, grid=-500)
    await _curtail(hass)
    await _until_probing(hass, freezer, None)
    _grid(hass, -100)
    await _tick(hass, freezer, None)
    control = _control(hass)
    assert control.probe_backoff == 60
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.home_curtailment"},
        blocking=True,
    )
    await hass.async_block_till_done()
    await _curtail(hass)
    assert control.probe_backoff == 0


async def test_a_meter_that_just_came_back_is_not_acted_on_for_half_a_minute(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """With an AC Battery the House needs half a minute of readings first."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=0)
    await _warm(hass, freezer, gx)
    await _curtail(hass)
    assert dtu_network.limits() == []
    # The meter goes away and returns with 500 W of export.
    hass.states.async_set("sensor.grid_meter", "unavailable")
    await _tick(hass, freezer, gx)
    _grid(hass, -500)
    await _tick(hass, freezer, gx)  # 15 s of readings: too few
    assert dtu_network.limits() == []
    await _tick(hass, freezer, gx)  # 30 s: the export is seen, once
    assert dtu_network.limits() == []
    await _tick(hass, freezer, gx)  # ... and again
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
