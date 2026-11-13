"""How often Grid Charging plans, and that a failing plan or listener stops nothing.

The set-up is that of ``test_grid_charging_flow``: 22:00 on 4 October 2026, an AC
Battery of 10 kWh, prices to the end of the 5th.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nulleinspeisung.charging_planner import plan_charging
from tests.conftest import DtuNetwork, SimTibber
from tests.test_grid_charging_flow import (
    _house,
    _prices,
    _settle,
    _setup,
    _value,
)

PLANNER = "custom_components.nulleinspeisung.grid_charging.plan_charging"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _counting():
    return patch(PLANNER, side_effect=plan_charging)


async def test_a_full_battery_is_not_planned_for_on_every_message_of_the_gx(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """At the Charge Target with nothing short (a full battery on a sunny day) the
    plan says "no shortfall", not "at target": it used to be made again for every
    message."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        level=100.0,
        daily_kwh=0.5,
        price_setup=lambda t: _prices(t, tomorrow={}),
    )
    assert _value(hass, "sensor", "grid_charging_state") == "no_shortfall"

    with _counting() as planner:
        for watts in range(1, 21):
            gx.charge(power=float(watts))
            await hass.async_block_till_done()
        assert planner.call_count == 0

        # Falling below the Charge Target is one change: one plan, not one per message.
        gx.charge(level=99.5)
        await hass.async_block_till_done()
        assert planner.call_count == 1
        for watts in range(21, 31):
            gx.charge(power=float(watts))
            await hass.async_block_till_done()
        assert planner.call_count == 1


async def test_the_triggers_of_one_quarter_hour_share_one_plan(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The clock, the prices, the Expected Load and the stale check of the boundary
    ask for one plan."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer)

    with _counting() as planner:
        await _settle(hass, freezer, gx, to="2026-10-04T22:15")
        assert planner.call_count == 1
        await _settle(hass, freezer, gx, to="2026-10-04T22:30")
        assert planner.call_count == 2


async def test_a_plan_that_fails_keeps_the_previous_plan_and_is_logged_once(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry, gx = await _setup(hass, dtu_network, tibber, freezer)
    charging = _house(entry).grid_charging
    plan = charging.plan
    assert plan is not None
    state = _value(hass, "sensor", "grid_charging_state")

    caplog.set_level(logging.ERROR)
    with patch(PLANNER, side_effect=RuntimeError("no plan today")):
        for level in (89.0, 88.0, 87.0, 86.0):
            gx.charge(level=level)
            await hass.async_block_till_done()
        assert charging.plan is plan
        assert _value(hass, "sensor", "grid_charging_state") == state

    failures = [r for r in caplog.records if "Planning the charging" in r.getMessage()]
    assert len(failures) == 1
    assert failures[0].exc_info is not None

    # It plans again as soon as it can.
    gx.charge(level=80.0)
    await hass.async_block_till_done()
    assert charging.plan is not plan


async def test_a_listener_of_the_gx_that_fails_does_not_stop_the_others(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry, gx = await _setup(hass, dtu_network, tibber, freezer)
    house = _house(entry)

    def broken() -> None:
        raise RuntimeError("broken listener")

    house.gateway._listeners._items.insert(0, broken)
    caplog.set_level(logging.ERROR)
    plan = house.grid_charging.plan
    gx.charge(level=80.0)
    await hass.async_block_till_done()
    gx.charge(level=70.0)
    await hass.async_block_till_done()

    assert house.grid_charging.plan is not plan
    assert house.battery.charge_level == 70.0
    broken_logs = [r for r in caplog.records if "broken listener" in str(r.exc_info)]
    assert len(broken_logs) == 1
