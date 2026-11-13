"""The BatteryLife Top-up end to end: simulated Tibber, GX, DTU and a set clock.

24 October 2026, 10:00 local (UTC+2). The AC Battery has 10 kWh and stands at
76 % with the Victron's active minimum at 75 % (the owner's case); the minimum the
owner configured is 20 %. The Expected Load is 100 W. The prices are normal at 0.40
but for 14:00 to 16:00 on the 24th and on the 25th, which are cheap at 0.10. 100 %
hold 2.5 kWh above the minimum; the battery has used up its 0.1 kWh by 14:00, so
the top-up (to 100 %, the default level) buys 2.5 / e kWh from the grid.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from tests.conftest import DtuNetwork, SimGx, SimTibber
from tests.test_grid_charging_flow import (
    ROOT,
    _call,
    _items,
    _local,
    _override,
    _settle,
    _setup,
    _state,
    _value,
    _writes,
)

ACTIVE = "system/0/Control/ActiveSocLimit"
CONFIGURED = "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"
TOP_UP_GRID = 2.5 / ROOT
SETPOINT_SLACK = 60.0
"""W the held setpoint may lag the power: a change under 50 W is not followed."""
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


async def test_after_the_battery_reached_the_level_today_no_top_up_until_tomorrow(
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

    # 12:00: the PV (or anything else) took the battery to 100 %.
    await _settle(hass, freezer, gx, to="2026-10-24T12:00")
    gx.charge(level=100.0)
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


# -- the Safety Buffer -------------------------------------------------------


async def _at_14(hass, dtu_network, tibber, freezer, **kw):
    """14:00: eight cheap quarter-hours are left, 2.1 kWh at half power, less than
    the top-up's 2.26 kWh; at full power four and a bit would do."""
    return await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-24T14:00",
        level=76.0,
        gx=_gx(),
        daily_kwh=2.4,
        price_setup=_one_day,
        switches=ON,
        **kw,
    )


async def test_the_safety_buffer_defaults_to_50_percent(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, _sim = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    assert float(_value(hass, "number", "safety_buffer")) == 50.0
    assert (
        next(iter(entry.runtime_data.houses.values())).grid_charging.safety_buffer
        == 50.0
    )
    assert _value(hass, "binary_sensor", "safety_buffer_active") == "off"


async def test_the_stored_safety_buffer_is_restored(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.home_safety_buffer", "20"),
                {
                    "native_max_value": 90,
                    "native_min_value": 0,
                    "native_step": 5,
                    "native_unit_of_measurement": "%",
                    "native_value": 20,
                },
            ),
        ],
    )
    entry, _sim = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    assert float(_value(hass, "number", "safety_buffer")) == 20.0
    house = next(iter(entry.runtime_data.houses.values()))
    assert house.grid_charging.safety_buffer == 20.0


async def test_the_top_up_level_defaults_to_100_percent(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, _sim = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    state = _state(hass, "number", "top_up_level")
    assert float(state.state) == 100.0
    assert (state.attributes["min"], state.attributes["max"]) == (90.0, 100.0)
    assert state.attributes["step"] == 1.0
    assert next(
        iter(entry.runtime_data.houses.values())
    ).grid_charging.top_up_level == (100.0)
    assert _energy(hass) == pytest.approx(2.5 / ROOT)


async def test_the_stored_top_up_level_is_restored(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.home_top_up_level", "95"),
                {
                    "native_max_value": 100,
                    "native_min_value": 90,
                    "native_step": 1,
                    "native_unit_of_measurement": "%",
                    "native_value": 95,
                },
            ),
        ],
    )
    entry, _sim = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    assert float(_value(hass, "number", "top_up_level")) == 95.0
    assert next(
        iter(entry.runtime_data.houses.values())
    ).grid_charging.top_up_level == (95.0)
    assert _energy(hass) == pytest.approx(2.0 / ROOT)


async def test_the_top_up_charges_to_the_level_that_is_set(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)
    assert _energy(hass) == pytest.approx(2.5 / ROOT)

    await _call(hass, "number", "set_value", "top_up_level", value=95)
    assert _energy(hass) == pytest.approx(2.0 / ROOT)
    await _call(hass, "number", "set_value", "top_up_level", value=90)
    assert _energy(hass) == pytest.approx(1.5 / ROOT)


async def test_reaching_the_level_that_is_set_counts_for_the_day(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """At 95 % set, 97 % reported is reached; at 100 % set it is not."""
    _entry, gx = await _start(
        hass, dtu_network, tibber, freezer, _gx(), days=_two_days, switches=ON
    )
    gx.charge(level=97.0)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is True

    await _call(hass, "number", "set_value", "top_up_level", value=95)
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is False
    assert _energy(hass) == 0.0


async def test_the_battery_is_held_from_the_first_top_up_quarter_to_the_level(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The top-up charges in the latest cheap quarter-hours (14:30 to 15:45 local
    on the 24th); from the first of them the battery does not discharge what was
    bought. Before it the cheap quarter-hours are not blocked. (Without the Safety
    Buffer, which would start charging earlier.)"""
    _entry, gx = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)
    await _call(hass, "number", "set_value", "safety_buffer", value=0)
    assert _value(hass, "binary_sensor", "discharge_block") == "off"
    await _settle(hass, freezer, gx, to="2026-10-24T14:29")
    assert _value(hass, "binary_sensor", "discharge_block") == "off"
    await _settle(hass, freezer, gx, to="2026-10-24T14:30")
    assert _value(hass, "binary_sensor", "discharge_block") == "on"
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    await _settle(hass, freezer, gx, to="2026-10-24T15:50")
    assert _value(hass, "binary_sensor", "discharge_block") == "on"
    await _settle(hass, freezer, gx, to="2026-10-24T16:00")
    assert _value(hass, "binary_sensor", "discharge_block") == "off"


async def test_when_half_power_without_sun_only_just_suffices_it_charges_now(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _at_14(hass, dtu_network, tibber, freezer)

    assert _value(hass, "binary_sensor", "safety_buffer_active") == "on"
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _override(gx) is not None
    assert _override(gx) > 2000.0


async def test_with_a_buffer_of_zero_the_full_power_suffices_later(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The same 14:00 as above: at full power four and a bit quarter-hours do, so
    the normal plan waits and the buffer forces nothing."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.home_safety_buffer", "0"),
                {
                    "native_max_value": 90,
                    "native_min_value": 0,
                    "native_step": 5,
                    "native_unit_of_measurement": "%",
                    "native_value": 0,
                },
            ),
        ],
    )
    _entry, gx = await _at_14(hass, dtu_network, tibber, freezer)

    assert _value(hass, "binary_sensor", "safety_buffer_active") == "off"
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _override(gx) is None


# -- steady charging by the Safety Buffer, and a window over midnight --------
#
# The evening of 24 October 2026. The Victron's minimum is 40 % (the owner's is
# 20 %), so 100 % hold 6.0 kWh above it. Expected Load 100 W. At 78.8 % the top-up
# needs 2.4 kWh from the grid. The prices of the evening are cheap from 21:30; what
# they do after midnight a test says.


def _evening(
    after_midnight: tuple[str, ...] = (), first: str = "21:30"
) -> Callable[[SimTibber], None]:
    """Cheap (0.10) from ``first`` to midnight and in the quarters of the 25th named
    in ``after_midnight``; every other quarter-hour is normal."""
    cheap = {
        f"{h:02d}:{m:02d}": (0.10, "CHEAP")
        for h in range(24)
        for m in (0, 15, 30, 45)
        if f"{h:02d}:{m:02d}" >= first
    }
    morning = dict.fromkeys(after_midnight, (0.1, "CHEAP"))

    def setup(tibber: SimTibber) -> None:
        tibber.today = _items(date(2026, 10, 24), cheap)
        tibber.tomorrow = _items(date(2026, 10, 25), morning)

    return setup


def _small_hours(until: str) -> tuple[str, ...]:
    """The quarter-hours of the 25th from midnight to ``until``."""
    return tuple(
        f"{h:02d}:{m:02d}"
        for h in range(24)
        for m in (0, 15, 30, 45)
        if f"{h:02d}:{m:02d}" < until
    )


async def _night(hass, dtu_network, tibber, freezer, at, price_setup, level=78.8):
    return await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at=at,
        level=level,
        gx=_gx(active=40.0),
        daily_kwh=2.4,
        price_setup=price_setup,
        switches=ON,
    )


def _buffer_power(hass: HomeAssistant) -> float:
    return float(_value(hass, "sensor", "buffer_charge_power"))


async def test_the_buffer_charges_steadily_with_the_override_held_between_quarters(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """21:30, ten cheap quarter-hours left, 2.4 kWh needed: 2.4 kWh in 2.5 h are
    960 W, at half the time 1920 W. Not 2100 W in every other quarter-hour."""
    _entry, gx = await _night(
        hass, dtu_network, tibber, freezer, "2026-10-24T21:30", _evening()
    )
    level = 78.8
    # The timer brings the setpoint to the current plan (the Expected Load was
    # set after the first one).
    await _settle(hass, freezer, gx, seconds=10)

    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _value(hass, "binary_sensor", "safety_buffer_active") == "on"
    assert _buffer_power(hass) == pytest.approx(1920.0, abs=5.0)
    # consumption 300 W minus production 200 W plus the charge power
    assert _override(gx) == pytest.approx(100.0 + 1920.0, abs=10.0)

    powers = [_buffer_power(hass)]
    for quarter in range(1, 8):
        # The battery took the power of the quarter-hour that ended.
        level += powers[-1] * 0.25 / 1000.0 * ROOT * 10.0
        gx.charge(level=level)
        minute = 21 * 60 + 30 + 15 * quarter
        await _settle(
            hass, freezer, gx, to=f"2026-10-24T{minute // 60:02d}:{minute % 60:02d}"
        )

        assert _value(hass, "sensor", "grid_charging_state") == "charging", quarter
        assert _value(hass, "binary_sensor", "safety_buffer_active") == "on", quarter
        assert 500.0 <= _buffer_power(hass) <= powers[-1] + 1.0, quarter
        powers.append(_buffer_power(hass))

    # Never released between two charged quarter-hours, and the setpoint follows
    # the falling power.
    assert None not in _writes(gx)
    assert _override(gx) < 2020.0
    # The setpoint is recomputed on the timer only, so it follows the power a little
    # late: never below it, and within a step of it.
    assert (
        100.0 + powers[-1] - SETPOINT_SLACK
        <= _override(gx)
        <= 100.0 + powers[-1] + 120.0
    )


async def test_the_setpoint_override_is_not_released_at_the_start_of_a_quarter(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A battery report at the very start of the quarter-hour comes before the
    replan of the boundary; the plan it still holds has no slot there."""
    _entry, gx = await _night(
        hass, dtu_network, tibber, freezer, "2026-10-24T21:30", _evening()
    )
    writes = len(_writes(gx))

    freezer.move_to(_local("2026-10-24T21:45"))
    gx.charge(level=78.9)
    await hass.async_block_till_done()

    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert None not in _writes(gx)
    assert len(_writes(gx)) <= writes + 1
    assert _override(gx) is not None


async def test_the_top_up_window_reaches_past_midnight_while_the_prices_stay_cheap(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """22:45. Cheap from 23:00 to 02:00 on the 25th: the top-up is charged in the latest
    quarter-hours of the window, after midnight, not squeezed in before it."""
    await _night(
        hass,
        dtu_network,
        tibber,
        freezer,
        "2026-10-24T22:45",
        _evening(_small_hours("02:00"), first="23:00"),
    )

    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is True
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    # 02:00 local is 00:00 UTC on the 25th; the latest quarters are charged.
    assert "2026-10-24T23:45:00+00:00" in slots
    assert all(slot < "2026-10-25T00:00" for slot in slots)
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"


async def test_without_cheap_quarters_after_midnight_the_window_ends_at_midnight(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _night(
        hass,
        dtu_network,
        tibber,
        freezer,
        "2026-10-24T22:45",
        _evening(first="23:00"),
    )

    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    assert slots
    assert all(slot < "2026-10-24T22:00" for slot in slots)


async def test_a_top_up_running_at_midnight_is_not_dropped_and_not_doubled(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _night(
        hass,
        dtu_network,
        tibber,
        freezer,
        "2026-10-24T22:45",
        _evening(_small_hours("02:00"), first="23:00"),
    )
    energy = _energy(hass)
    assert energy > 0.0

    # Midnight: a new local day, the top-up is still wanted, in the same window.
    await _settle(hass, freezer, gx, to="2026-10-25T00:00")
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is True
    assert _energy(hass) > 0.0
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    assert all(slot < "2026-10-25T00:00" for slot in slots)

    # 00:30: the battery has reached 100 % (in the new day): that is the new day's
    # top-up, so none is planned for the rest of it.
    await _settle(hass, freezer, gx, to="2026-10-25T00:30")
    gx.charge(level=100.0)
    await hass.async_block_till_done()
    assert _energy(hass) == 0.0
    assert _state(hass, "sensor", "top_up_energy").attributes["wanted"] is False
    await _settle(hass, freezer, gx, to="2026-10-25T01:00")
    gx.charge(level=90.0)
    await _settle(hass, freezer, gx, to="2026-10-25T01:15")
    assert _energy(hass) == 0.0
