"""Grid Charging end to end: simulated Tibber, GX, DTU and a set clock.

The House is in Berlin (52.5 N, 13.4 E). The night starts at 22:00 on 4 October
2026; the next sunrise is about 07:14 local time (UTC+2). The AC Battery has
10 kWh and charges at 2100 W, the efficiency is 78 %. The Victron's minimum
stands at 20 %. Unless a test says otherwise the Expected Load is the fallback of
10 kWh a day (417 W), no PV Forecast is used, prices are known from 22:00 on the
4th to the end of the 5th (104 quarter-hours, all normal at 0.40 but for the ones
a test names), so the battery is short of energy before the day is over and the
plan is limited by the cheap quarter-hours alone.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.nulleinspeisung import solar
from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import (
    HOME_1,
    DtuNetwork,
    SimDtu,
    SimGx,
    SimHouse,
    SimTibber,
    setup_entry,
)

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
POWER = "system/0/Dc/Battery/Power"
SETPOINT = "hub4/0/Overrides/Setpoint"
DYNAMIC_ESS = "settings/0/Settings/DynamicEss/Mode"
NORMAL = (0.40, "NORMAL")
ROOT = math.sqrt(0.78)
TZ_OFFSET = timedelta(hours=2)
LOAD_KWH = 10.0 / 24 / 4
"""The Expected Load in a quarter-hour, kWh."""
DRAIN_SLOTS = 99
"""Of the 104 quarter-hours to the end of the 5th, 99 do not qualify (the five
of NIGHT_PRICES are Discharge Blocks)."""
GRID_PER_SLOT = 0.525
"""kWh from the grid in a quarter-hour at 2100 W."""


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


# -- the world -------------------------------------------------------------


def _items(day: date, special: dict[str, tuple[float, str]]) -> list[dict[str, Any]]:
    """The 96 quarter-hours of a local day: normal at 0.40 but for ``special``."""
    items = []
    for i in range(96):
        hhmm = f"{i // 4:02d}:{i % 4 * 15:02d}"
        total, level = special.get(hhmm, NORMAL)
        items.append(
            {
                "total": total,
                "startsAt": f"{day.isoformat()}T{hhmm}:00.000+02:00",
                "currency": "EUR",
                "level": level,
            }
        )
    return items


def _prices(
    tibber: SimTibber,
    tonight: dict[str, tuple[float, str]] | None = None,
    tomorrow: dict[str, tuple[float, str]] | None = None,
) -> None:
    """Prices for 4 and 5 October; times in ``tomorrow`` are of the 5th."""
    tibber.today = _items(date(2026, 10, 4), tonight or {})
    tibber.tomorrow = _items(date(2026, 10, 5), tomorrow or {})


NIGHT_PRICES = {
    "02:00": (0.10, "VERY_CHEAP"),
    "02:15": (0.11, "VERY_CHEAP"),
    "03:30": (0.12, "VERY_CHEAP"),
    "03:45": (0.13, "CHEAP"),
    "04:00": (0.35, "CHEAP"),  # cheap by level, but not worth it at 78 %
}
BLOCKED_PRICES = {f"02:{m:02d}": (0.38, "CHEAP") for m in (0, 15, 30, 45)}


def _local(iso: str) -> datetime:
    """A time of 2026-10-04 or later as written in Berlin, UTC+2."""
    return datetime.fromisoformat(iso + "+02:00")


async def _clock(hass: HomeAssistant, freezer, iso: str) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(_local(iso))


def _dtu(oma: float = 200.0, buero: float = 0.0) -> SimDtu:
    """The two assigned Inverters, producing ``oma`` and ``buero`` watts."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (oma, buero), strict=False):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


def _grid(hass: HomeAssistant, watts: float | str) -> None:
    hass.states.async_set(METER, str(watts), {"unit_of_measurement": "W"})


async def _setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    freezer,
    *,
    at: str = "2026-10-04T22:00",
    level: float = 90.0,
    grid: float = 100.0,
    gx: SimGx | None = None,
    switches: dict[str, str | None] | None = None,
    prices: dict[str, tuple[float, str]] | None = None,
    production: tuple[float, float] = (200.0, 0.0),
    daily_kwh: float | None = None,
    active_minimum: float | None = None,
    price_setup: Callable[[SimTibber], None] | None = None,
    **house: Any,
):
    await _clock(hass, freezer, at)
    if price_setup is not None:
        price_setup(tibber)
    else:
        _prices(tibber, tomorrow=prices if prices is not None else NIGHT_PRICES)
    gx = gx or SimGx()
    if active_minimum is not None:
        gx.topics["system/0/Control/ActiveSocLimit"] = json.dumps(
            {"value": active_minimum}
        )
    gx.charge(level=level, power=0.0)
    _grid(hass, grid)
    stored = {
        "curtailment": "off",
        "publish_grid_power": "on",
        "grid_charging": "on",
        **(switches or {}),
    }
    entry = await setup_entry(
        hass,
        dtu_network,
        _dtu(*production),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                tibber_home=HOME_1,
                gx=gx,
                capacity=10.0,
                grid_topic="grid/test/house",
                stored_switches=stored,
                **house,
            )
        ],
        tibber=tibber,
    )
    if daily_kwh is not None:
        next(iter(entry.runtime_data.houses.values())).expected_load.set_fallback_kwh(
            daily_kwh
        )
        await hass.async_block_till_done()
    return entry, gx


def _state(hass: HomeAssistant, domain: str, key: str) -> State:
    state = hass.states.get(f"{domain}.home_{key}")
    assert state is not None, f"{domain}.home_{key}"
    return state


def _value(hass: HomeAssistant, domain: str, key: str) -> str:
    return _state(hass, domain, key).state


def _writes(gx: SimGx) -> list[float | None]:
    """The values written to the setpoint override, in order."""
    return [
        json.loads(payload)["value"]
        for topic, payload in gx.other_publishes
        if topic.endswith(SETPOINT)
    ]


def _override(gx: SimGx) -> float | None:
    return json.loads(gx.topics[SETPOINT])["value"]


async def _settle(
    hass: HomeAssistant, freezer, gx: SimGx, *, to: str | None = None, seconds=0.0
) -> None:
    """Move the clock; the Grid Meter and the GX keep reporting, as they do."""
    if to is not None:
        freezer.move_to(_local(to))
    else:
        freezer.tick(timedelta(seconds=seconds))
    meter = hass.states.get(METER)
    if meter is not None:
        hass.states.async_set(METER, meter.state, meter.attributes)
    if not gx.dead and not gx.down:
        gx.set(POWER, json.loads(gx.topics[POWER])["value"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _call(
    hass: HomeAssistant, domain: str, service: str, key: str, **data: Any
) -> None:
    await hass.services.async_call(
        domain, service, {"entity_id": f"{domain}.home_{key}", **data}, blocking=True
    )
    await hass.async_block_till_done()


def _house(entry):
    return next(iter(entry.runtime_data.houses.values()))


# -- the night, step by step -----------------------------------------------


async def test_a_night_with_cheap_slots_is_charged_in_the_planned_quarter_hours(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Planned at 22:00, written at 02:00, followed, released, written again."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer)

    # The plan at 22:00: 7 kWh above the minimum carry the House (417 W, taken
    # out divided by e) until about 15:00 on the 5th; the rest of the day is a
    # shortfall at 0.40. The cheap quarter-hours that are worth it (<= 0.78 *
    # 0.40 = 0.312) are 02:00, 02:15, 03:30 and 03:45 (the 0.35 one at 04:00 is
    # not), they hold 4 * 0.525 kWh and do not nearly cover the shortfall.
    shortfall = DRAIN_SLOTS * LOAD_KWH - 7.0 * ROOT
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"
    assert float(_value(hass, "sensor", "energy_missing")) == pytest.approx(1.0)
    assert float(_value(hass, "sensor", "expensive_shortfall")) == pytest.approx(
        shortfall
    )
    assert float(_value(hass, "sensor", "energy_to_buy")) == pytest.approx(
        4 * GRID_PER_SLOT
    )
    assert float(_value(hass, "sensor", "uncovered_shortfall")) == pytest.approx(
        shortfall - 4 * GRID_PER_SLOT * 0.78
    )
    assert float(_value(hass, "sensor", "reference_price")) == pytest.approx(0.40)
    next_start = _state(hass, "sensor", "next_charging_start")
    assert next_start.state == "2026-10-05T00:00:00+00:00"  # 02:00 local
    assert next_start.attributes["slots"] == [
        "2026-10-05T00:00:00+00:00",
        "2026-10-05T00:15:00+00:00",
        "2026-10-05T01:30:00+00:00",
        "2026-10-05T01:45:00+00:00",
    ]
    assert _writes(gx) == []

    # 02:00: the first planned quarter-hour starts.
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    # consumption 300 W (grid 100 + production 200) minus production 200 plus
    # the Maximum Charge Power 2100 W
    assert _writes(gx) == [2200.0]
    assert _override(gx) == 2200.0
    assert _value(hass, "sensor", "next_charging_start") == "2026-10-05T00:00:00+00:00"

    # The battery follows: 2100 W in, the House imports it on top of its load.
    _grid(hass, 2200)
    gx.charge(power=2100.0)
    await _settle(hass, freezer, gx, seconds=15)
    assert _writes(gx) == [2200.0]

    # The load grows by 100 W (more than 50 W). It is not followed when the
    # battery reports, only when the 10 second timer comes round.
    _grid(hass, 2300)
    gx.charge(power=2100.0)
    await hass.async_block_till_done()
    assert _writes(gx) == [2200.0]
    await _settle(hass, freezer, gx, seconds=10)
    assert _writes(gx) == [2200.0, 2300.0]
    assert _override(gx) == 2300.0

    # A change of 40 W is not followed, nor is one of 10 W.
    _grid(hass, 2340)
    await _settle(hass, freezer, gx, seconds=10)
    _grid(hass, 2310)
    await _settle(hass, freezer, gx, seconds=10)
    assert _writes(gx) == [2200.0, 2300.0]

    # 02:15: still planned; nothing changes on the GX.
    gx.charge(level=94.0)
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0, 2300.0]

    # 02:30: the next planned slot is 03:30. The override is released.
    _grid(hass, 100)
    gx.charge(power=0.0)
    gx.charge(level=97.0)
    await _settle(hass, freezer, gx, to="2026-10-05T02:30")
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _writes(gx) == [2200.0, 2300.0, None]
    assert _override(gx) is None
    assert _value(hass, "sensor", "next_charging_start") == "2026-10-05T01:30:00+00:00"

    # Nothing is written while waiting.
    await _settle(hass, freezer, gx, seconds=60)
    assert _writes(gx) == [2200.0, 2300.0, None]

    # 03:30: charging again.
    await _settle(hass, freezer, gx, to="2026-10-05T03:30")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0, 2300.0, None, 2200.0]


CHEAP_PAIR = {"02:00": (0.10, "VERY_CHEAP"), "02:15": (0.11, "VERY_CHEAP")}
"""Two adjacent cheap quarter-hours: nothing drains the battery between them."""


async def test_the_target_reached_releases_the_battery(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The battery got full during a planned quarter-hour: release at once."""
    _entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, level=99.0, prices=CHEAP_PAIR
    )
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    gx.charge(level=100.0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "target_reached"
    assert _override(gx) is None
    assert _value(hass, "sensor", "next_charging_start") == "unknown"
    assert float(_value(hass, "sensor", "energy_to_buy")) == 0.0
    # The shortfall is still there; there is just no room for more energy.
    assert float(_value(hass, "sensor", "expensive_shortfall")) > 0.0


async def test_a_full_battery_waits_for_a_quarter_hour_after_it_has_drained(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Full at 02:00, but the House uses 0.5 kWh before 03:30: charging goes there."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer, level=99.0)
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    gx.charge(level=100.0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _override(gx) is None
    # 02:30 to 03:30: four quarter-hours of 0.104 kWh at the meter leave room.
    attributes = _state(hass, "sensor", "next_charging_start").attributes
    assert attributes["slots"][0] == "2026-10-05T01:30:00+00:00"


async def test_no_charging_the_charge_target_is_reached_from_below_one_point(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A target of 80 % is reached at 80.4 %: the plan ends without waiting."""
    _entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, level=78.0, prices=CHEAP_PAIR
    )
    await _call(hass, "number", "set_value", "charge_target", value=80)
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    gx.charge(level=80.4)  # less than a point from the level planned with
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "target_reached"
    assert _override(gx) is None


# -- the evening of 2026-10-24 ---------------------------------------------

REAL_DAY = (
    ("00:00", "07:00", 0.05, "VERY_CHEAP"),  # a very cheap night
    ("07:00", "09:00", 0.02, "CHEAP"),  # cheap right after sunrise
    ("09:00", "17:00", 0.10, "CHEAP"),
    ("17:00", "21:00", 0.256, "EXPENSIVE"),  # the expensive evening
    ("21:00", "24:00", 0.05, "VERY_CHEAP"),
)


def _real_day_prices(tibber: SimTibber) -> None:
    special = {}
    for first, last, price, level in REAL_DAY:
        moment = datetime.fromisoformat(f"2026-10-24T{first}:00")
        end = (
            datetime.fromisoformat(f"2026-10-24T{last}:00")
            if last != "24:00"
            else (datetime.fromisoformat("2026-10-25T00:00:00"))
        )
        while moment < end:
            special[moment.strftime("%H:%M")] = (price, level)
            moment += timedelta(minutes=15)
    tibber.today = _items(date(2026, 10, 24), special)
    tibber.tomorrow = []


async def test_the_evening_is_prepared_for_in_the_cheap_morning(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """01:00, battery at 76 % with the Victron's minimum at 75 %, 300 W round the
    clock, a very cheap night, the cheapest quarter-hours right after sunrise and
    an expensive evening (0.256). The old rule needed 0.06 kWh at sunrise and
    bought nothing; the evening needs 1.2 kWh, 0.1 kWh above the minimum carry
    0.088 kWh of it."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-24T01:00",
        level=76.0,
        active_minimum=75.0,
        daily_kwh=7.2,
        price_setup=_real_day_prices,
    )
    shortfall = 4 * 0.3 - 0.1 * ROOT
    assert _value(hass, "binary_sensor", "discharge_block") == "on"
    assert float(_value(hass, "sensor", "expensive_shortfall")) == pytest.approx(
        shortfall
    )
    assert float(_value(hass, "sensor", "uncovered_shortfall")) == pytest.approx(0.0)
    assert float(_value(hass, "sensor", "energy_to_buy")) == pytest.approx(
        shortfall / 0.78
    )
    assert float(_value(hass, "sensor", "reference_price")) == pytest.approx(0.256)
    assert float(_value(hass, "sensor", "battery_at_sunrise")) == pytest.approx(0.1)
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    # Bought in the cheapest quarter-hours (08:15 to 08:45 local, 0.02), not at
    # night (0.05), the later ones first.
    next_start = _state(hass, "sensor", "next_charging_start")
    assert next_start.state == "2026-10-24T06:15:00+00:00"
    assert next_start.attributes["slots"] == [
        "2026-10-24T06:15:00+00:00",
        "2026-10-24T06:30:00+00:00",
        "2026-10-24T06:45:00+00:00",
    ]
    assert _writes(gx) == []

    # The night passes without a write.
    await _settle(hass, freezer, gx, to="2026-10-24T06:00")
    assert _writes(gx) == []

    # 08:15: the first planned quarter-hour; the setpoint override is written.
    await _settle(hass, freezer, gx, to="2026-10-24T08:15")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]

    # The first quarter-hour was planned for 0.375 kWh from the grid: 0.33 kWh
    # stored, 3.3 points. The later quarter-hours are full (0.4637 kWh, 4.6
    # points), and the plan never needs more.
    await _settle(hass, freezer, gx, to="2026-10-24T08:30")
    _grid(hass, 2200)  # the House imports its load and the battery's charge
    gx.charge(level=79.3, power=2100.0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    await _settle(hass, freezer, gx, to="2026-10-24T08:45")
    gx.charge(level=83.9)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]

    # 09:00: 89 % is 1.4 kWh above the minimum, enough for the evening.
    await _settle(hass, freezer, gx, to="2026-10-24T09:00")
    _grid(hass, 100)
    gx.charge(level=89.0, power=0.0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "no_shortfall"
    assert float(_value(hass, "sensor", "expensive_shortfall")) == 0.0
    assert float(_value(hass, "sensor", "energy_to_buy")) == 0.0
    assert _value(hass, "sensor", "reference_price") == "unknown"
    assert _writes(gx) == [2200.0, None]


async def test_the_night_is_used_when_the_morning_is_not_cheaper(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The same day with the morning dearer than the night: charging is at night."""

    def prices(tibber: SimTibber) -> None:
        _real_day_prices(tibber)
        for item in tibber.today:
            if "T07:" in item["startsAt"] or "T08:" in item["startsAt"]:
                item["total"], item["level"] = 0.12, "CHEAP"

    _entry, _gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-24T01:00",
        level=76.0,
        active_minimum=75.0,
        daily_kwh=7.2,
        price_setup=prices,
    )
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    # 0.05 at night is the cheapest: the latest night quarter-hours go first.
    assert slots == [
        "2026-10-24T04:15:00+00:00",
        "2026-10-24T04:30:00+00:00",
        "2026-10-24T04:45:00+00:00",
    ]


# -- the running quarter-hour keeps its place ---------------------------------

THREE_CHEAP = {f"02:{m:02d}": (0.10, "VERY_CHEAP") for m in (0, 15, 30)}
"""Equal prices: without care every replan would move the charging to 02:30."""
NEED_LEVEL = 38.76
"""At 02:15 with 100 W, 0.6 kWh from the grid are needed."""


async def _minutes(hass, freezer, gx, entry, count: int, level: float) -> float:
    """Charge for ``count`` minutes, replanning every minute; the level it reaches."""
    for _ in range(count):
        level += 0.309  # 2100 W for a minute: 0.035 kWh * e stored, in points
        gx.charge(level=level, power=2100.0)
        await _settle(hass, freezer, gx, seconds=60)
        _house(entry).grid_charging._replan()
        await hass.async_block_till_done()
    return level


async def test_a_replan_every_minute_keeps_the_running_quarter_hour(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:15",
        level=NEED_LEVEL,
        daily_kwh=2.4,
        prices=THREE_CHEAP,
    )
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]
    _grid(hass, 2200)
    level = await _minutes(hass, freezer, gx, entry, 14, NEED_LEVEL)
    assert level > NEED_LEVEL + 4.0
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]  # written once, never released


async def test_a_cheaper_later_quarter_hour_waits_for_the_running_one_to_end(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    prices = {
        "02:00": (0.12, "VERY_CHEAP"),
        "02:15": (0.12, "VERY_CHEAP"),
        "02:30": (0.10, "VERY_CHEAP"),
    }
    entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:15",
        level=NEED_LEVEL,
        daily_kwh=2.4,
        prices=prices,
    )
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    _grid(hass, 2200)
    level = await _minutes(hass, freezer, gx, entry, 14, NEED_LEVEL)
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]
    # the quarter-hour ends: the cheaper 02:30 takes over what is left
    await _settle(hass, freezer, gx, to="2026-10-05T02:30")
    gx.charge(level=level + 0.5)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "next_charging_start").state == (
        "2026-10-05T00:30:00+00:00"
    )
    assert _writes(gx)[0] == 2200.0


async def test_reaching_the_target_inside_the_running_quarter_hour_ends_charging(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:15",
        level=NEED_LEVEL,
        daily_kwh=7.2,
        prices=THREE_CHEAP,
    )
    await _call(hass, "number", "set_value", "charge_target", value=40)
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    gx.charge(level=40.1)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "target_reached"
    assert _override(gx) is None


# -- the efficiency block --------------------------------------------------


async def test_the_efficiency_blocks_charging_and_it_is_shown(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Cheap by level but dearer than 78 % of the reference: nothing is written."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer, prices=BLOCKED_PRICES)
    assert _value(hass, "sensor", "grid_charging_state") == "blocked_by_efficiency"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "on"
    assert float(_value(hass, "sensor", "reference_price")) == pytest.approx(0.40)
    assert float(_value(hass, "sensor", "energy_to_buy")) == 0.0
    assert float(_value(hass, "sensor", "energy_missing")) == pytest.approx(1.0)
    assert _value(hass, "sensor", "next_charging_start") == "unknown"

    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    await _settle(hass, freezer, gx, to="2026-10-05T02:30")
    assert _value(hass, "sensor", "grid_charging_state") == "blocked_by_efficiency"
    assert _writes(gx) == []  # (the cheap quarter-hours do hold a discharge block)


async def test_a_better_efficiency_lifts_the_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """At 96 % the threshold is 0.384: 0.38 passes, the plan is made at once."""
    _entry, _gx = await _setup(
        hass, dtu_network, tibber, freezer, prices=BLOCKED_PRICES
    )
    await _call(hass, "number", "set_value", "battery_efficiency", value=96)
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"


async def test_ignoring_the_efficiency_charges_the_same_night(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """With the ignore switch on, the prices that blocked now charge."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        prices=BLOCKED_PRICES,
        switches={"ignore_efficiency": "on"},
    )
    assert _value(hass, "switch", "ignore_efficiency") == "on"
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"
    # All four quarter-hours cost the same; the later ones are preferred, since
    # energy bought later is stored for less time. The plan of 22:00 has all
    # four; at 02:00 the first is the one running, so it keeps its place.
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]


async def test_the_ignore_switch_works_while_blocked(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, _gx = await _setup(
        hass, dtu_network, tibber, freezer, prices=BLOCKED_PRICES
    )
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "on"
    await _call(hass, "switch", "turn_on", "ignore_efficiency")
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    await _call(hass, "switch", "turn_off", "ignore_efficiency")
    assert _value(hass, "sensor", "grid_charging_state") == "blocked_by_efficiency"


async def test_no_qualifying_quarter_hour_is_not_the_efficiency(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Only normal slots tonight: no slot qualifies, which is a state of its own."""
    _entry, _gx = await _setup(hass, dtu_network, tibber, freezer, prices={})
    assert _value(hass, "sensor", "grid_charging_state") == "no_qualifying_slot"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"
    assert float(_value(hass, "sensor", "uncovered_shortfall")) > 0.0
    await _call(
        hass,
        "select",
        "select_option",
        "charge_price_levels",
        option="normal_and_below",
    )
    # Now every quarter-hour qualifies, so the House never has to buy at a price
    # it cannot choose: there is no shortfall and nothing to compare with.
    assert _value(hass, "sensor", "reference_price") == "unknown"
    assert float(_value(hass, "sensor", "expensive_shortfall")) == 0.0
    assert _value(hass, "sensor", "grid_charging_state") == "no_shortfall"


# -- the price levels ------------------------------------------------------


async def test_the_price_level_choice_decides_which_slots_qualify(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Very cheap only: 02:00, 02:15, 03:30 (and no CHEAP 03:45)."""
    entry, _gx = await _setup(hass, dtu_network, tibber, freezer, level=70.0)
    # The cheap and very cheap slots that are worth it are 02:00, 02:15, 03:30,
    # 03:45: they hold 2.1 kWh, the shortfall is far more.
    plan = _house(entry).grid_charging.plan
    assert len(plan.slots) == 4
    assert plan.uncovered > 0.0
    await _call(
        hass, "select", "select_option", "charge_price_levels", option="very_cheap"
    )
    plan = _house(entry).grid_charging.plan
    assert [s.start.astimezone(UTC).strftime("%H:%M") for s in plan.slots] == [
        "00:00",
        "00:15",
        "01:30",
    ]


# -- the horizon -----------------------------------------------------------


async def test_the_plan_is_not_bound_by_sunrise(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The cheapest slots of all are after sunrise: they are planned, for the
    shortfall that comes after them (the evening), and the nights' are kept for
    what comes before them."""
    after_sunrise = {
        f"{h:02d}:{m:02d}": (0.01, "VERY_CHEAP")
        for h in range(7, 12)
        for m in (0, 15, 30, 45)
    }
    entry, _gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        level=20.0,
        prices={**NIGHT_PRICES, **after_sunrise},
    )
    house = _house(entry)
    sunrise = solar.next_sunrise(52.5, 13.4, datetime(2026, 10, 4, 20, 0, tzinfo=UTC))
    assert house.grid_charging.deadline == sunrise
    assert (
        datetime(2026, 10, 5, 5, 5, tzinfo=UTC)
        < sunrise
        < datetime(2026, 10, 5, 5, 25, tzinfo=UTC)
    )
    plan = house.grid_charging.plan
    after = [s for s in plan.slots if s.start >= sunrise]
    before = [s for s in plan.slots if s.start < sunrise]
    # Level 20 % is the minimum: nothing is usable. The 16 quarter-hours until
    # 02:00 have no cheap quarter-hour before them (uncovered). The shortfall
    # from 04:15 to sunrise is covered by the night, the day after 12:00 by
    # the 0.01 quarter-hours at the end of the morning (latest first).
    assert len(before) == 4
    assert len(after) == 13
    assert {s.price for s in after} == {0.01}
    assert max(s.start for s in after) == datetime(2026, 10, 5, 9, 45, tzinfo=UTC)
    assert plan.uncovered == pytest.approx(16 * LOAD_KWH)
    attributes = _state(hass, "sensor", "next_charging_start").attributes
    assert len(attributes["slots"]) <= 96
    assert attributes["slots"] == sorted(attributes["slots"])


# -- safety ----------------------------------------------------------------


async def _charging(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer, **kw
):
    """A House that is charging, at 02:00, with the override written."""
    entry, gx = await _setup(hass, dtu_network, tibber, freezer, **kw)
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _override(gx) == 2200.0
    return entry, gx


async def test_switching_grid_charging_off_releases_at_once(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    await _call(hass, "switch", "turn_off", "grid_charging")
    assert _writes(gx) == [2200.0, None]
    assert _override(gx) is None
    assert _value(hass, "sensor", "grid_charging_state") == "off"
    assert _house(entry).wanted_overrides.setpoint is None
    # The plan is still shown while it is off.
    assert _value(hass, "sensor", "next_charging_start") != "unknown"
    await _settle(hass, freezer, gx, seconds=60)
    assert _writes(gx) == [2200.0, None]


async def test_switching_it_on_inside_a_planned_slot_charges_at_once(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        switches={"grid_charging": "off"},
    )
    assert _value(hass, "sensor", "grid_charging_state") == "off"
    assert _writes(gx) == []
    await _call(hass, "switch", "turn_on", "grid_charging")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]


async def test_losing_the_prices_releases_at_once(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The stored prices are gone and Tibber does not answer: nothing charges."""
    entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    tibber.down = True
    _house(entry).prices.prices = ()
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    assert _value(hass, "sensor", "grid_charging_state") == "no_prices"
    assert _writes(gx) == [2200.0, None]
    assert _override(gx) is None
    assert _house(entry).wanted_overrides.setpoint is None


async def test_a_silent_battery_releases(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    gx.dead = True
    await _settle(hass, freezer, gx, seconds=90)
    await _settle(hass, freezer, gx, seconds=15)
    assert _value(hass, "sensor", "grid_charging_state") == "battery_unavailable"
    assert _house(entry).wanted_overrides.setpoint is None
    assert _writes(gx)[-1] is None


async def test_switching_off_the_hand_over_releases_what_we_set(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The owner takes the battery back: our override goes, then silence."""
    entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    await _call(hass, "switch", "turn_off", "publish_grid_power")
    assert _value(hass, "sensor", "grid_charging_state") == "not_in_control"
    assert _writes(gx) == [2200.0, None]
    assert _override(gx) is None
    assert _house(entry).wanted_overrides.setpoint is None
    # From here on nothing is written, whatever happens on the GX.
    gx.set(SETPOINT, 500)
    await _settle(hass, freezer, gx, seconds=60)
    await _settle(hass, freezer, gx, to="2026-10-05T03:30")
    assert _writes(gx) == [2200.0, None]
    assert _override(gx) == 500


async def test_without_the_hand_over_nothing_is_ever_written(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Stored off: another system may own the battery; even in a planned slot."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        switches={"publish_grid_power": "off"},
    )
    gx.set(SETPOINT, 300)
    await _settle(hass, freezer, gx, seconds=30)
    assert _value(hass, "sensor", "grid_charging_state") == "not_in_control"
    assert gx.other_publishes == []
    assert _override(gx) == 300


async def test_a_switch_that_was_never_stored_is_off_and_nothing_is_written(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A new House: Grid Charging spends money, so it starts off. Even in a slot."""
    entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        switches={"grid_charging": None},
    )
    assert _value(hass, "switch", "grid_charging") == "off"
    assert _value(hass, "switch", "ignore_efficiency") == "off"
    assert _value(hass, "sensor", "grid_charging_state") == "off"
    assert gx.other_publishes == []
    assert _house(entry).grid_charging.charge_target == 100
    assert _house(entry).grid_charging.battery_efficiency == 78
    assert _value(hass, "number", "charge_target") == "100.0"
    assert _value(hass, "number", "battery_efficiency") == "78.0"
    assert _value(hass, "select", "charge_price_levels") == "cheap_and_below"


async def test_starting_inside_a_planned_slot_charges_only_after_restoring(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A restart in the middle of a slot: the write comes once, with the value."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer, at="2026-10-05T02:05")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0]


async def test_dynamic_ess_stops_the_charging(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    gx.set(DYNAMIC_ESS, 1)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "dynamic_ess_active"
    assert _writes(gx) == [2200.0, None]
    gx.set(DYNAMIC_ESS, 0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _writes(gx) == [2200.0, None, 2200.0]


async def test_unknown_consumption_charges_with_the_maximum_charge_power_alone(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _charging(hass, dtu_network, tibber, freezer)
    _grid(hass, "unavailable")
    await _settle(hass, freezer, gx, seconds=15)
    assert _writes(gx)[-1] == 2100.0


async def test_the_override_is_written_again_when_the_gx_does_not_confirm(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A silent GX gets the value again, but not within ten seconds."""
    gx = SimGx()
    gx.accepts_writes = False
    _entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, at="2026-10-05T02:05", gx=gx
    )
    assert _writes(gx) == [2200.0]
    await _settle(hass, freezer, gx, seconds=5)
    assert _writes(gx) == [2200.0]
    await _settle(hass, freezer, gx, seconds=10)
    assert _writes(gx) == [2200.0, 2200.0]


# -- a restless load -------------------------------------------------------------


MAX_WRITES_PER_SLOT = 3
"""Bound asserted below: the first write, one real step of the load, one spare.

The timer alone would allow 90 writes in a quarter-hour; a hysteresis of 50 W
against a load that moves by up to 40 W every few seconds allows only these.
"""


async def test_a_restless_load_causes_few_writes_in_a_slot(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The load wobbles by up to 40 W every 3 s and steps up by 120 W once."""
    _entry, gx = await _setup(hass, dtu_network, tibber, freezer)
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _writes(gx) == [2200.0]
    wobble = (0, 40, -30, 25, -40, 10, -20, 35)
    for i in range(1, 290):  # 14.5 minutes in steps of 3 seconds
        step = 120 if i > 140 else 0
        noise = wobble[i % len(wobble)]
        _grid(hass, 2200 + step + noise)
        gx.charge(power=2100.0 + wobble[(i * 3) % len(wobble)] / 2)
        await _settle(hass, freezer, gx, seconds=3)
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    writes = _writes(gx)
    assert 2 <= len(writes) <= MAX_WRITES_PER_SLOT
    assert writes[-1] == pytest.approx(2320, abs=60)


# -- Curtailment and Grid Charging together --------------------------------


async def test_curtailment_and_grid_charging_do_not_fight(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The House exports 700 W and is curtailed; charging makes it import: 100 %.

    The limits rise and stay at 100 % for as long as the charging lasts, and the
    setpoint override stays where it is: nothing oscillates.
    """
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        grid=-700.0,
        production=(300.0, 700.0),
        switches={"curtailment": "on"},
    )
    # The House averages a minute of the meter and lowers only export that
    # persists: half a minute of readings, a first run that waits, a second.
    for _ in range(4):
        await _settle(hass, freezer, gx, seconds=15)
    lowered = dtu_network.limits()
    assert lowered
    assert {percent for _, _, percent in lowered} != {100}
    dtu_network.clear_limits()

    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    # 300 W load, 1000 W of PV, 2100 W into the battery: 1400 W from the grid.
    assert _writes(gx) == [1400.0]
    _grid(hass, 1400)
    gx.charge(power=2100.0)
    for _ in range(8):
        await _settle(hass, freezer, gx, seconds=15)
    raised = dtu_network.limits()
    assert raised
    assert {percent for _, _, percent in raised[-2:]} == {100}
    count = len(raised)
    for _ in range(8):
        await _settle(hass, freezer, gx, seconds=15)
    assert len(dtu_network.limits()) == count
    assert _writes(gx) == [1400.0]
    assert _value(hass, "sensor", "control_state") in ("holding", "raising")


async def test_after_the_charging_curtailment_takes_over_again(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """When the slot is over the House exports again and is curtailed as before."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        level=96.0,
        grid=-700.0,
        production=(300.0, 700.0),
        switches={"curtailment": "on"},
    )
    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    _grid(hass, 1400)
    gx.charge(power=2100.0)
    for _ in range(5):  # the mean of the last minute follows the import
        await _settle(hass, freezer, gx, seconds=15)
    assert {p for _, _, p in dtu_network.limits()[-2:]} == {100}
    dtu_network.clear_limits()

    # The battery is full: the plan ends and the House exports again.
    _grid(hass, -700)
    gx.charge(power=0.0)
    gx.charge(level=100.0)
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    for _ in range(5):
        await _settle(hass, freezer, gx, seconds=15)
    assert _override(gx) is None
    assert dtu_network.limits()
    assert {p for _, _, p in dtu_network.limits()} != {100}


# -- the entities ----------------------------------------------------------


async def test_the_charging_device_sits_below_the_house(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, _gx = await _setup(hass, dtu_network, tibber, freezer)
    registry = dr.async_get(hass)
    house = registry.async_get_device_by_identifier(
        (DOMAIN, "house-home"), entry.entry_id
    )
    device = registry.async_get_device_by_identifier(
        (DOMAIN, "house-home_charging"), entry.entry_id
    )
    assert house is not None
    assert device is not None
    assert device.name == "Home Grid charging"
    assert device.via_device_id == house.id
    assert device.config_subentry_id == house.config_subentry_id
    assert device.model == "Grid charging"
    entities = er.async_entries_for_device(er.async_get(hass), device.id)
    assert {e.unique_id for e in entities} == {
        f"house-home_{key}"
        for key in (
            "discharge_block",
            "grid_charging",
            "ignore_efficiency",
            "charge_target",
            "battery_efficiency",
            "charge_price_levels",
            "charging_blocked_by_efficiency",
            "grid_charging_state",
            "next_charging_start",
            "energy_to_buy",
            "energy_missing",
            "reference_price",
            "use_forecast",
            "forecast_share",
            "expensive_shortfall",
            "uncovered_shortfall",
            "battery_at_sunrise",
            "forecast_surplus",
            "forecast_in_use",
        )
    }
    assert {e.entity_id for e in entities} == {
        "switch.home_grid_charging",
        "switch.home_ignore_efficiency",
        "number.home_charge_target",
        "number.home_battery_efficiency",
        "select.home_charge_price_levels",
        "binary_sensor.home_charging_blocked_by_efficiency",
        "binary_sensor.home_discharge_block",
        "sensor.home_grid_charging_state",
        "sensor.home_next_charging_start",
        "sensor.home_energy_to_buy",
        "sensor.home_energy_missing",
        "sensor.home_reference_price",
        "switch.home_use_forecast",
        "number.home_forecast_share",
        "sensor.home_expensive_shortfall",
        "sensor.home_uncovered_shortfall",
        "sensor.home_battery_at_sunrise",
        "sensor.home_forecast_surplus",
        "binary_sensor.home_forecast_in_use",
    }


async def test_the_retired_sensor_is_removed_from_the_registry(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The sensor of the old rule is gone after the update, not left unavailable."""
    entry, _gx = await _setup(hass, dtu_network, tibber, freezer)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "house-home_energy_needed_at_sunrise",
        config_entry=entry,
        suggested_object_id="home_energy_needed_at_sunrise",
    )
    assert registry.async_get("sensor.home_energy_needed_at_sunrise") is not None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("sensor.home_energy_needed_at_sunrise") is None
    assert hass.states.get("sensor.home_energy_needed_at_sunrise") is None
    assert hass.states.get("sensor.home_expensive_shortfall") is not None


async def test_the_charging_device_is_named_in_german(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    hass.config.language = "de"
    entry, _gx = await _setup(hass, dtu_network, tibber, freezer)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "house-home_charging"), entry.entry_id
    )
    assert device.name == "Home Netzladen"


async def test_the_settings_have_their_ranges_and_units(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, _gx = await _setup(hass, dtu_network, tibber, freezer)
    target = _state(hass, "number", "charge_target").attributes
    assert (target["min"], target["max"], target["step"]) == (10, 100, 1)
    assert target["unit_of_measurement"] == "%"
    efficiency = _state(hass, "number", "battery_efficiency").attributes
    assert (efficiency["min"], efficiency["max"], efficiency["step"]) == (50, 100, 1)
    levels = _state(hass, "select", "charge_price_levels").attributes
    assert levels["options"] == ["very_cheap", "cheap_and_below", "normal_and_below"]
    blocked = _state(hass, "binary_sensor", "charging_blocked_by_efficiency")
    assert "device_class" not in blocked.attributes
    state = _state(hass, "sensor", "grid_charging_state").attributes
    assert state["device_class"] == "enum"
    assert "waiting" in state["options"]
    assert "dynamic_ess_active" in state["options"]
    price = _state(hass, "sensor", "reference_price").attributes
    assert price["unit_of_measurement"] == "EUR/kWh"
    energy = _state(hass, "sensor", "energy_to_buy").attributes
    assert energy["unit_of_measurement"] == "kWh"


async def test_the_settings_are_restored(
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
                State("number.home_charge_target", "80"),
                {
                    "native_max_value": 100,
                    "native_min_value": 10,
                    "native_step": 1,
                    "native_unit_of_measurement": "%",
                    "native_value": 80,
                },
            ),
            (
                State("number.home_battery_efficiency", "90"),
                {
                    "native_max_value": 100,
                    "native_min_value": 50,
                    "native_step": 1,
                    "native_unit_of_measurement": "%",
                    "native_value": 90,
                },
            ),
            (State("select.home_charge_price_levels", "normal_and_below"), {}),
            (State("switch.home_ignore_efficiency", "on"), {}),
        ],
    )
    entry, _gx = await _setup(hass, dtu_network, tibber, freezer)
    charging = _house(entry).grid_charging
    assert charging.charge_target == 80
    assert charging.battery_efficiency == 90
    assert charging.price_levels == "normal_and_below"
    assert charging.ignore_efficiency
    assert charging.enabled


async def test_a_house_without_prices_or_capacity_has_no_charging_device(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _clock(hass, freezer, "2026-10-04T22:00")
    gx = SimGx()
    entry = await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse("Home", gx=gx, capacity=10.0, grid_topic="grid/test/house"),
            SimHouse("Other", tibber_home=HOME_1, gx=SimGx("gx2.test")),
            SimHouse("Third", tibber_home=HOME_1),
        ],
        tibber=tibber,
    )
    for house in entry.runtime_data.houses.values():
        assert house.grid_charging is None
    registry = dr.async_get(hass)
    for name in ("home", "other", "third"):
        assert (
            registry.async_get_device_by_identifier(
                (DOMAIN, f"house-{name}_charging"), entry.entry_id
            )
            is None
        )
    assert hass.states.get("switch.home_grid_charging") is None


async def test_the_slots_attribute_is_small(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A whole night of slots: at most 96 entries, ISO strings."""
    everything = {
        f"{h:02d}:{m:02d}": (0.01, "VERY_CHEAP")
        for h in range(0, 6)
        for m in (0, 15, 30, 45)
    }
    _entry, _gx = await _setup(
        hass, dtu_network, tibber, freezer, level=0.0, prices=everything
    )
    slots = _state(hass, "sensor", "next_charging_start").attributes["slots"]
    assert 0 < len(slots) <= 96
    assert all(datetime.fromisoformat(s).tzinfo is not None for s in slots)
    assert slots == sorted(slots)


# -- the sunrise -----------------------------------------------------------


def test_next_sunrise_is_the_next_one_in_utc() -> None:
    before = solar.next_sunrise(52.5, 13.4, datetime(2026, 10, 4, 20, 0, tzinfo=UTC))
    assert before.tzinfo == UTC
    assert (
        datetime(2026, 10, 5, 5, 5, tzinfo=UTC)
        < before
        < datetime(2026, 10, 5, 5, 25, tzinfo=UTC)
    )
    after = solar.next_sunrise(52.5, 13.4, before + timedelta(minutes=1))
    assert (
        datetime(2026, 10, 6, 5, 5, tzinfo=UTC)
        < after
        < datetime(2026, 10, 6, 5, 25, tzinfo=UTC)
    )
    # just before sunrise the same sunrise is still the next one
    assert solar.next_sunrise(52.5, 13.4, before - timedelta(minutes=1)) == before


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
        datetime(2026, 12, 21, 12, 0, tzinfo=UTC),
    ],
)
def test_without_a_sunrise_it_is_a_day_later(when: datetime) -> None:
    """Midnight sun and polar night: now plus 24 hours."""
    assert solar.next_sunrise(80.0, 15.0, when) == when + timedelta(hours=24)
