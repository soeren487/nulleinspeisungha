"""The BatteryLife Top-up end to end: simulated Tibber, GX, DTU and a set clock.

24 October 2026, 10:00 local (UTC+2). The AC Battery has 10 kWh and stands at
76 % with the Victron's active minimum at 75 % (the owner's case); the minimum the
owner configured is 20 %. The Expected Load is 100 W. The prices are normal at 0.40
but for 14:00 to 16:00 on the 24th and on the 25th, which are cheap at 0.10. 95 %
hold 2.0 kWh above the minimum; the battery has used up its 0.1 kWh by 14:00, so
the top-up buys 2.0 / e kWh from the grid.
"""

from __future__ import annotations

import json
import math
from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from tests.conftest import DtuNetwork, SimGx, SimTibber
from tests.test_grid_charging_flow import (
    ROOT,
    _call,
    _items,
    _settle,
    _setup,
    _state,
    _value,
)

ACTIVE = "system/0/Control/ActiveSocLimit"
CONFIGURED = "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"
TOP_UP_GRID = 2.0 / ROOT
ON = {"batterylife_top_up": None}
"""The switch has no stored position: on, as it is by default."""
CHEAP = {f"{h:02d}:{m:02d}": (0.10, "CHEAP") for h in (14, 15) for m in (0, 15, 30, 45)}


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _one_day(tibber: SimTibber) -> None:
    """Prices to the end of the 24th: the top-up is all there is to plan."""
    tibber.today = _items(date(2026, 10, 24), CHEAP)
    tibber.tomorrow = []


def _two_days(tibber: SimTibber) -> None:
    tibber.today = _items(date(2026, 10, 24), CHEAP)
    tibber.tomorrow = _items(date(2026, 10, 25), CHEAP)


def _gx(active: float = 75.0, configured: float = 20.0) -> SimGx:
    gx = SimGx()
    gx.topics[ACTIVE] = json.dumps({"value": active})
    gx.topics[CONFIGURED] = json.dumps({"value": configured})
    return gx


async def _start(hass, dtu_network, tibber, freezer, gx, days=_one_day, **kw):
    return await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-24T10:00",
        level=76.0,
        gx=gx,
        daily_kwh=2.4,
        price_setup=days,
        **kw,
    )


def _energy(hass: HomeAssistant) -> float:
    return float(_value(hass, "sensor", "top_up_energy"))


async def test_a_raised_minimum_plans_the_top_up_in_the_cheap_quarters_of_today(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    assert _value(hass, "switch", "batterylife_top_up") == "on"
    assert _energy(hass) == pytest.approx(TOP_UP_GRID)
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is True
    assert float(_value(hass, "sensor", "energy_to_buy")) == pytest.approx(TOP_UP_GRID)
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    # Cheap quarters of today only (14:00 to 16:00 local = 12:00 to 14:00 UTC),
    # the later ones first: 4.3 quarters of 0.525 kWh.
    assert len(slots) == math.ceil(TOP_UP_GRID / 0.525)
    assert all("2026-10-24T12:00" <= slot < "2026-10-24T14:00" for slot in slots)
    assert "2026-10-24T13:45:00+00:00" in slots


async def test_without_a_raised_minimum_there_is_no_top_up(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(
        hass,
        dtu_network,
        tibber,
        freezer,
        _gx(active=20.0, configured=20.0),
        switches=ON,
    )
    gx_state = _state(hass, "sensor", "top_up_energy")

    assert float(gx_state.state) == 0.0
    assert gx_state.attributes["wanted"] is False


async def test_the_switch_turns_the_top_up_off_and_on(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)
    assert _energy(hass) == pytest.approx(TOP_UP_GRID)

    await _call(hass, "switch", "turn_off", "batterylife_top_up")
    assert _value(hass, "switch", "batterylife_top_up") == "off"
    assert _energy(hass) == 0.0
    # What is left is what the shortfalls need, a good deal less than the top-up.
    assert float(_value(hass, "sensor", "energy_to_buy")) < TOP_UP_GRID / 2
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is False

    await _call(hass, "switch", "turn_on", "batterylife_top_up")
    assert _energy(hass) == pytest.approx(TOP_UP_GRID)


async def test_a_stored_off_position_is_restored(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(
        hass,
        dtu_network,
        tibber,
        freezer,
        _gx(),
        switches={"batterylife_top_up": "off"},
    )

    assert _value(hass, "switch", "batterylife_top_up") == "off"
    assert _energy(hass) == 0.0


async def test_after_the_battery_reached_95_today_there_is_no_top_up_until_tomorrow(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _start(
        hass, dtu_network, tibber, freezer, _gx(), days=_two_days, switches=ON
    )
    assert _energy(hass) == pytest.approx(TOP_UP_GRID)

    # 12:00: the PV (or anything else) took the battery to 95 %.
    await _settle(hass, freezer, gx, to="2026-10-24T12:00")
    gx.charge(level=95.0)
    await hass.async_block_till_done()
    assert _energy(hass) == 0.0
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is False

    # It is remembered when the level falls again, also across quarter-hours.
    await _settle(hass, freezer, gx, to="2026-10-24T13:00")
    gx.charge(level=80.0)
    await _settle(hass, freezer, gx, to="2026-10-24T13:15")
    assert _energy(hass) == 0.0

    # The next local day the minimum is still raised and the top-up is wanted
    # again, in that day's cheap quarters.
    await _settle(hass, freezer, gx, to="2026-10-25T10:00")
    gx.charge(level=76.0)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is True
    assert _energy(hass) == pytest.approx(TOP_UP_GRID)
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    assert all("2026-10-25T12:00" <= slot < "2026-10-25T14:00" for slot in slots)
