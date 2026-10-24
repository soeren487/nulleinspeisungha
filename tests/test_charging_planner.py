"""The Charging Plan, tested directly: no Home Assistant, the time is passed in.

Every quarter-hour of a test day is written as a segment of the day:
``(from, to, price, qualifies, load W, PV W, support W)``. The battery has 10
kWh, charges at 2100 W (0.525 kWh from the grid in a quarter-hour) and has a
Battery Efficiency of 78 %, so ``e`` = sqrt(0.78) = 0.8832: a kWh bought stores
0.8832 kWh and delivers 0.78 kWh later.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nulleinspeisung.charging_planner import (
    BatteryModel,
    ChargingPlan,
    PlanReason,
    Quarter,
    discharge_block_active,
    plan_charging,
    slot_length,
)
from custom_components.nulleinspeisung.price_source import PriceLevel

T0 = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
QUARTER = timedelta(minutes=15)
NIGHT = T0
E = math.sqrt(0.78)
GRID_PER_QUARTER = 0.525
"""kWh from the grid at 2100 W in a full quarter-hour."""


@dataclass(frozen=True)
class Price:
    start: datetime
    total: float
    level: str


def prices(
    start: datetime,
    values: list[tuple[float, str]],
    step: timedelta = QUARTER,
) -> list[Price]:
    """Consecutive slots from ``start``, one per ``(price, level)``."""
    return [Price(start + step * i, p, lvl) for i, (p, lvl) in enumerate(values)]


def night(values: list[float], level: str = "cheap", start: datetime = T0) -> list:
    return prices(start, [(v, level) for v in values])


def at(hhmm: str) -> datetime:
    """A time of the test day (hours may go beyond 24)."""
    hours, minutes = hhmm.split(":")
    return T0 + timedelta(hours=int(hours), minutes=int(minutes))


def day(*segments: tuple) -> list[Quarter]:
    """The quarters of consecutive segments; PV and support default to 0."""
    quarters = []
    for first, last, price, qualifies, load, *rest in segments:
        pv = rest[0] if rest else 0.0
        support = rest[1] if len(rest) > 1 else 0.0
        moment = at(first)
        while moment < at(last):
            quarters.append(Quarter(moment, price, qualifies, load, pv, support))
            moment += QUARTER
    return quarters


def battery(**kw) -> BatteryModel:
    base = {
        "capacity_kwh": 10.0,
        "charge_level": 50.0,
        "floor": 0.0,
        "charge_target": 100.0,
        "max_charge_power": 2100.0,
        "efficiency": 0.78,
    }
    return BatteryModel(**(base | kw))


def starts(result: ChargingPlan) -> list[str]:
    return [s.start.strftime("%H:%M") for s in result.slots]


def grid_at(result: ChargingPlan, hhmm: str) -> float:
    return next(s.grid_kwh for s in result.slots if s.start == at(hhmm))


# -- the owner's case of 2026-10-24 ------------------------------------------

REAL_DAY = (
    ("00:00", "07:00", 0.05, True, 300.0),  # very cheap night, Discharge Block
    ("07:00", "09:00", 0.02, True, 300.0),  # cheap right after sunrise
    ("09:00", "17:00", 0.20, False, 300.0, 300.0),  # the PV carries the House
    ("17:00", "21:00", 0.256, False, 500.0),  # the expensive evening
    ("21:00", "24:00", 0.05, True, 300.0),
)
REAL_SHORTFALL = 2.0 - 0.1 * E
"""The evening needs 4 h * 500 W = 2.0 kWh; 0.1 kWh above the floor deliver 0.0883."""


def real_plan(**kw) -> ChargingPlan:
    """01:00, 76 % with the Victron's minimum at 75 %: 0.1 kWh are usable."""
    settings = {"charge_level": 76.0, "floor": 75.0} | kw.pop("battery", {})
    return plan_charging(
        at("01:00"),
        kw.pop("quarters", day(*REAL_DAY)[4:]),
        battery(**settings),
        sunrise=kw.pop("sunrise", at("06:00")),
    )


def test_the_real_case_is_prepared_for_in_the_cheap_morning_not_the_night() -> None:
    result = real_plan()

    assert result.reason is PlanReason.CHARGING_PLANNED
    assert result.shortfall == pytest.approx(REAL_SHORTFALL)
    assert result.uncovered == pytest.approx(0.0, abs=1e-6)
    assert result.reference_price == pytest.approx(0.256)
    # A kWh bought delivers 0.78 kWh later.
    assert result.energy_to_buy == pytest.approx(REAL_SHORTFALL / 0.78)
    assert result.energy_to_buy == pytest.approx(2.4509, abs=1e-4)
    # The morning (0.02) is cheaper than the night (0.05): only the morning is
    # used, from its end backwards (equal prices: the later quarter-hour).
    assert starts(result) == ["07:45", "08:00", "08:15", "08:30", "08:45"]
    assert [s.price for s in result.slots] == [0.02] * 5
    assert grid_at(result, "07:45") == pytest.approx(
        REAL_SHORTFALL / 0.78 - 4 * GRID_PER_QUARTER
    )
    for slot in result.slots[1:]:
        assert slot.grid_kwh == pytest.approx(GRID_PER_QUARTER)
        assert slot.stored_kwh == pytest.approx(GRID_PER_QUARTER * E)
        assert slot.end - slot.start == QUARTER
    assert not any(s.start < at("07:00") for s in result.slots)


def test_the_content_at_sunrise_and_at_the_end_follow_the_planned_charging() -> None:
    before_charging = real_plan(sunrise=at("06:00"))
    assert before_charging.battery_at_sunrise == pytest.approx(0.1)
    after_charging = real_plan(sunrise=at("12:00"))
    assert after_charging.battery_at_sunrise == pytest.approx(
        0.1 + 2.4509 * E, abs=1e-3
    )
    # Beyond the known prices: the content at the end of the horizon, which the
    # evening has used up exactly.
    assert real_plan(sunrise=at("48:00")).battery_at_sunrise == pytest.approx(
        0.0, abs=1e-6
    )
    assert real_plan(sunrise=None).battery_at_sunrise is None


def test_the_slot_length_comes_from_the_data() -> None:
    hourly = prices(T0, [(0.2, "cheap")] * 8, step=timedelta(hours=1))
    assert slot_length(hourly) == timedelta(hours=1)


def test_one_price_has_the_default_slot_length() -> None:
    assert slot_length(night([0.2])) == QUARTER
    assert slot_length([]) == QUARTER


def test_the_night_is_used_for_what_the_morning_cannot_carry() -> None:
    """Only two morning quarter-hours qualify: the rest comes from the latest night."""
    quarters = day(
        ("01:00", "07:00", 0.05, True, 300.0),
        ("07:00", "08:30", 0.20, False, 300.0, 300.0),
        ("08:30", "09:00", 0.02, True, 300.0),
        ("09:00", "17:00", 0.20, False, 300.0, 300.0),
        ("17:00", "21:00", 0.256, False, 500.0),
    )
    result = real_plan(quarters=quarters)
    assert result.uncovered == pytest.approx(0.0, abs=1e-6)
    assert starts(result) == ["06:15", "06:30", "06:45", "08:30", "08:45"]
    for hhmm in ("08:30", "08:45", "06:30", "06:45"):
        assert grid_at(result, hhmm) == pytest.approx(GRID_PER_QUARTER)
    assert grid_at(result, "06:15") == pytest.approx(
        REAL_SHORTFALL / 0.78 - 4 * GRID_PER_QUARTER
    )


def test_a_cheap_night_and_morning_without_an_expensive_period_plan_nothing() -> None:
    quarters = day(
        ("01:00", "07:00", 0.05, True, 300.0),
        ("07:00", "09:00", 0.02, True, 300.0),
        ("09:00", "17:00", 0.20, False, 300.0, 400.0),  # PV above the load
        ("17:00", "24:00", 0.05, True, 500.0),
    )
    result = real_plan(quarters=quarters)
    assert result.reason is PlanReason.NO_SHORTFALL
    assert result.slots == ()
    assert result.energy_to_buy == 0.0
    assert result.shortfall == 0.0
    assert result.uncovered == 0.0
    assert result.reference_price is None


def test_a_cheap_night_is_not_charged_in_when_the_battery_is_not_needed() -> None:
    """Nothing runs dry before the next cheap period, so nothing is bought."""
    quarters = day(
        ("01:00", "06:00", 0.04, True, 300.0),
        ("06:00", "08:00", 0.30, False, 300.0),  # 0.6 kWh at the meter
        ("08:00", "12:00", 0.04, True, 300.0),
    )
    result = real_plan(quarters=quarters, battery={"charge_level": 90.0, "floor": 20.0})
    # 7 kWh above the floor carry the two normal hours.
    assert result.reason is PlanReason.NO_SHORTFALL
    assert result.slots == ()


# -- the worth test, per shortfall ----------------------------------------------


def one_cheap_slot(price: float, shortfall_price: float = 0.40) -> list[Quarter]:
    return day(
        ("00:00", "00:15", price, True, 0.0),
        ("00:15", "01:00", 0.30, False, 0.0),
        ("01:00", "02:00", shortfall_price, False, 500.0),
    )


def at_floor(**kw) -> BatteryModel:
    return battery(charge_level=0.0, **kw)


def test_a_price_of_exactly_efficiency_times_the_shortfall_price_passes() -> None:
    result = plan_charging(T0, one_cheap_slot(0.312), at_floor())
    assert starts(result) == ["00:00"]
    assert result.reason is PlanReason.CHARGING_PLANNED
    # 0.5 kWh shortfall would need 0.641 kWh; 0.525 kWh fit in the quarter-hour
    assert grid_at(result, "00:00") == pytest.approx(GRID_PER_QUARTER)
    assert result.uncovered == pytest.approx(0.5 - 0.525 * 0.78)


def test_a_price_just_above_it_does_not_pass() -> None:
    result = plan_charging(T0, one_cheap_slot(0.3121), at_floor())
    assert result.slots == ()
    assert result.reason is PlanReason.BLOCKED_BY_EFFICIENCY
    assert result.uncovered == pytest.approx(result.shortfall)
    assert result.shortfall == pytest.approx(0.5)


def test_every_shortfall_is_compared_with_its_own_price() -> None:
    """0.20 passes against 0.40 (limit 0.312) but not against 0.22 (limit 0.1716).

    The first shortfall (0.22) has no worthwhile quarter-hour before it and stays
    uncovered; the second (0.40) is covered from the quarter-hours after the
    first, never from the one before it, because that would feed the first.
    """
    quarters = day(
        ("00:00", "01:00", 0.20, True, 0.0),
        ("01:00", "02:00", 0.22, False, 1000.0),
        ("02:00", "03:00", 0.20, True, 0.0),
        ("03:00", "04:00", 0.40, False, 500.0),
    )
    result = plan_charging(T0, quarters, at_floor())
    assert result.shortfall == pytest.approx(1.5)
    assert result.reference_price == pytest.approx((1.0 * 0.22 + 0.5 * 0.40) / 1.5)
    assert result.uncovered == pytest.approx(1.0)
    assert starts(result) == ["02:30", "02:45"]
    assert result.energy_to_buy == pytest.approx(0.5 / 0.78)
    assert grid_at(result, "02:45") == pytest.approx(GRID_PER_QUARTER)
    assert grid_at(result, "02:30") == pytest.approx(0.5 / 0.78 - GRID_PER_QUARTER)


def test_ignoring_the_efficiency_charges_where_it_would_not_be_worth_it() -> None:
    result = plan_charging(T0, one_cheap_slot(0.35), at_floor(ignore_efficiency=True))
    assert starts(result) == ["00:00"]
    assert result.uncovered == pytest.approx(0.5 - 0.525 * 0.78)


def test_with_the_efficiency_at_100_percent_the_energy_bought_is_the_shortfall() -> (
    None
):
    result = plan_charging(T0, one_cheap_slot(0.40), at_floor(efficiency=1.0))
    assert result.energy_to_buy == pytest.approx(0.5)
    assert result.slots[0].stored_kwh == pytest.approx(0.5)


# -- why nothing is planned ---------------------------------------------------------


def test_blocked_by_efficiency_is_not_no_qualifying_slot() -> None:
    blocked = plan_charging(T0, one_cheap_slot(0.35), at_floor())
    assert blocked.reason is PlanReason.BLOCKED_BY_EFFICIENCY
    nothing = plan_charging(
        T0,
        day(
            ("00:00", "01:00", 0.30, False, 0.0), ("01:00", "02:00", 0.40, False, 500.0)
        ),
        at_floor(),
    )
    assert nothing.reason is PlanReason.NO_QUALIFYING_SLOT
    assert nothing.shortfall == pytest.approx(0.5)
    assert nothing.uncovered == pytest.approx(0.5)


def test_a_qualifying_quarter_hour_after_the_shortfall_does_not_help() -> None:
    quarters = day(
        ("00:00", "01:00", 0.40, False, 500.0),
        ("01:00", "02:00", 0.01, True, 0.0),
    )
    result = plan_charging(T0, quarters, at_floor())
    assert result.reason is PlanReason.NO_QUALIFYING_SLOT
    assert result.slots == ()


def test_no_quarter_hours_are_no_prices() -> None:
    result = plan_charging(T0, [], battery())
    assert result.reason is PlanReason.NO_PRICES
    assert result.slots == ()
    assert result.energy_missing == pytest.approx(5.0)


def test_quarter_hours_that_are_over_are_no_prices() -> None:
    quarters = day(("00:00", "01:00", 0.40, False, 500.0))
    assert (
        plan_charging(at("01:00"), quarters, battery()).reason is PlanReason.NO_PRICES
    )


def test_a_full_battery_that_still_faces_a_shortfall_is_at_the_target() -> None:
    quarters = day(
        ("00:00", "01:00", 0.05, True, 300.0),
        ("01:00", "05:00", 0.40, False, 3000.0),
    )
    result = plan_charging(T0, quarters, battery(charge_level=100.0))
    assert result.shortfall > 0.0
    assert result.reason is PlanReason.AT_TARGET
    assert result.slots == ()
    assert result.energy_missing == 0.0


def test_a_level_above_a_lower_target_is_at_the_target() -> None:
    quarters = day(
        ("00:00", "01:00", 0.05, True, 300.0),
        ("01:00", "05:00", 0.40, False, 2000.0),
    )
    result = plan_charging(T0, quarters, battery(charge_level=60.0, charge_target=50.0))
    assert result.reason is PlanReason.AT_TARGET
    assert result.slots == ()


# -- the limits ------------------------------------------------------------------------

EVENING = (
    ("00:00", "03:00", 0.05, True, 300.0),
    ("03:00", "05:00", 0.40, False, 3000.0),  # 6 kWh at the meter
)


def test_the_charge_target_limits_what_is_bought() -> None:
    """Content 5.0 kWh, target 60 %: only 1.0 kWh more may be stored."""
    result = plan_charging(T0, day(*EVENING), battery(charge_target=60.0))
    assert result.shortfall == pytest.approx(6.0 - 5.0 * E)
    assert result.energy_to_buy == pytest.approx(1.0 / E)
    assert sum(s.stored_kwh for s in result.slots) == pytest.approx(1.0)
    assert result.uncovered == pytest.approx(6.0 - 6.0 * E)
    assert result.uncovered > 0.0
    # latest quarter-hours first (equal prices)
    assert starts(result) == ["02:15", "02:30", "02:45"]


def test_the_maximum_charge_power_limits_every_quarter_hour() -> None:
    quarters = day(
        ("00:00", "00:30", 0.05, True, 300.0),
        ("00:30", "02:30", 0.40, False, 3000.0),
    )
    result = plan_charging(
        T0,
        quarters,
        battery(),
    )
    assert [s.grid_kwh for s in result.slots] == [pytest.approx(GRID_PER_QUARTER)] * 2
    assert result.energy_to_buy == pytest.approx(1.05)
    assert result.uncovered == pytest.approx(result.shortfall - 1.05 * 0.78)
    slow = plan_charging(T0, quarters, battery(max_charge_power=1000.0))
    assert [s.grid_kwh for s in slow.slots] == [pytest.approx(0.25)] * 2


def test_a_battery_with_no_charge_power_plans_nothing_and_stops() -> None:
    result = plan_charging(T0, day(*EVENING), battery(max_charge_power=0.0))
    assert result.slots == ()
    assert result.uncovered == pytest.approx(result.shortfall)


def test_the_floor_is_never_discharged() -> None:
    """76 % with a minimum of 75 % holds 0.1 kWh, not 7.6 kWh."""
    assert real_plan().shortfall == pytest.approx(REAL_SHORTFALL)
    without_floor = real_plan(battery={"floor": 0.0})
    assert without_floor.reason is PlanReason.NO_SHORTFALL


def test_below_the_floor_nothing_is_usable() -> None:
    result = real_plan(battery={"charge_level": 70.0})
    assert result.shortfall == pytest.approx(2.0)
    assert result.energy_to_buy == pytest.approx(2.0 / 0.78)


def test_the_charge_target_counts_above_the_floor() -> None:
    """Floor 75 %, target 80 %: 0.5 kWh of content at most (10 kWh * 5 %)."""
    result = real_plan(battery={"charge_target": 80.0})
    assert sum(s.stored_kwh for s in result.slots) == pytest.approx(0.4)
    assert result.uncovered == pytest.approx(2.0 - 0.5 * E)


# -- PV and DC Battery Support ---------------------------------------------------------


def test_pv_surplus_fills_the_battery_before_the_evening() -> None:
    quarters = day(
        ("08:00", "09:00", 0.05, True, 300.0),
        ("09:00", "13:00", 0.20, False, 300.0, 1500.0),  # 4.8 kWh surplus
        ("17:00", "21:00", 0.256, False, 500.0),
    )
    quarters = day(
        ("08:00", "09:00", 0.05, True, 300.0),
        ("09:00", "13:00", 0.20, False, 300.0, 1500.0),
        ("13:00", "17:00", 0.20, False, 300.0, 300.0),
        ("17:00", "21:00", 0.256, False, 500.0),
    )
    result = plan_charging(at("08:00"), quarters, at_floor())
    # 4.8 kWh surplus * 0.8832 = 4.24 kWh in the battery; the evening needs 2.26.
    assert result.reason is PlanReason.NO_SHORTFALL
    assert result.slots == ()


def test_pv_surplus_charges_a_held_battery_in_a_qualifying_quarter_hour() -> None:
    quarters = day(
        ("08:00", "10:00", 0.05, True, 300.0, 1300.0),  # 1 kW surplus, 2 kWh
        ("10:00", "11:00", 0.20, False, 1000.0),
    )
    held = plan_charging(at("08:00"), quarters, at_floor())
    assert held.reason is PlanReason.NO_SHORTFALL
    free = plan_charging(at("08:00"), quarters, at_floor(discharge_blocks=False))
    # Without blocks the battery takes the same surplus; the result is the same.
    assert free.reason is PlanReason.NO_SHORTFALL


def test_pv_surplus_beyond_the_capacity_is_lost_and_charging_cannot_help() -> None:
    quarters = day(
        ("00:00", "01:00", 0.05, True, 300.0),
        ("01:00", "05:00", 0.20, False, 300.0, 5000.0),  # far more than fits
        ("05:00", "06:00", 0.40, False, 12000.0),
    )
    result = plan_charging(T0, quarters, battery(capacity_kwh=2.0, charge_level=0.0))
    # Full (2 kWh) by the evening whatever is bought: 12 - 2 * 0.78 uncovered.
    assert result.shortfall == pytest.approx(12.0 - 2.0 * E)
    assert result.slots == ()
    assert result.uncovered == pytest.approx(result.shortfall)
    assert result.reason is PlanReason.AT_TARGET


def test_dc_battery_support_lowers_the_shortfall() -> None:
    without = real_plan()
    quarters = day(*REAL_DAY)[4:]
    with_support = real_plan(
        quarters=[
            Quarter(q.start, q.price, q.qualifies, q.load_w, q.pv_w, 200.0)
            if at("17:00") <= q.start < at("21:00")
            else q
            for q in quarters
        ]
    )
    # 300 W instead of 500 W for 4 hours: 1.2 kWh instead of 2.0
    assert with_support.shortfall == pytest.approx(1.2 - 0.1 * E)
    assert with_support.shortfall < without.shortfall
    assert with_support.energy_to_buy == pytest.approx(with_support.shortfall / 0.78)


def test_support_above_the_load_charges_the_battery() -> None:
    quarters = day(
        ("00:00", "01:00", 0.05, True, 300.0),
        ("01:00", "02:00", 0.30, False, 100.0, 0.0, 900.0),  # 800 W surplus
        ("02:00", "03:00", 0.40, False, 500.0),
    )
    result = plan_charging(T0, quarters, at_floor())
    # 0.8 kWh * 0.8832 stored, delivers 0.8 * 0.78 = 0.624 of the 0.5 needed
    assert result.reason is PlanReason.NO_SHORTFALL


# -- the current quarter-hour and the horizon ------------------------------------------


def test_the_current_quarter_hour_counts_with_what_remains_of_it() -> None:
    quarters = day(
        ("00:00", "00:15", 0.05, True, 300.0),
        ("00:15", "02:00", 0.40, False, 2000.0),
    )
    now = T0 + timedelta(minutes=10)  # a third of the quarter-hour is left
    result = plan_charging(now, quarters, at_floor())
    assert starts(result) == ["00:00"]
    assert grid_at(result, "00:00") == pytest.approx(2.1 * 0.25 / 3)
    assert result.slots[0].start == T0
    assert result.is_due(now)
    assert not result.is_due(T0 + QUARTER)


def test_the_load_of_the_current_quarter_hour_counts_with_what_remains() -> None:
    quarters = day(("00:00", "00:15", 0.40, False, 800.0))
    now = T0 + timedelta(minutes=7, seconds=30)  # half of it
    result = plan_charging(now, quarters, at_floor())
    assert result.shortfall == pytest.approx(0.8 * 0.125)


def test_quarter_hours_48_hours_ahead_or_later_are_not_planned() -> None:
    quarters = day(
        ("00:00", "48:00", 0.05, True, 300.0),
        ("48:00", "50:00", 0.40, False, 3000.0),
    )
    result = plan_charging(T0, quarters, at_floor())
    assert result.reason is PlanReason.NO_SHORTFALL
    inside = day(
        ("00:00", "47:45", 0.05, True, 300.0),
        ("47:45", "48:00", 0.40, False, 3000.0),
    )
    assert plan_charging(T0, inside, at_floor()).shortfall == pytest.approx(0.75)


TWO_DAYS = (
    ("00:00", "06:00", 0.05, True, 0.0),  # cheap period A
    ("06:00", "18:00", 0.20, False, 0.0),
    ("18:00", "20:00", 0.30, False, 1500.0),  # evening 1: 3 kWh
    ("20:00", "30:00", 0.05, True, 0.0),  # cheap period B
    ("30:00", "42:00", 0.20, False, 0.0),
    ("42:00", "44:00", 0.30, False, 1500.0),  # evening 2: 3 kWh
    ("44:00", "48:00", 0.20, False, 0.0),
)


def test_two_expensive_evenings_are_covered_from_the_cheap_period_before_each() -> None:
    result = plan_charging(T0, day(*TWO_DAYS), at_floor())
    assert result.reason is PlanReason.CHARGING_PLANNED
    assert result.shortfall == pytest.approx(6.0)
    assert result.uncovered == pytest.approx(0.0, abs=1e-6)
    assert result.reference_price == pytest.approx(0.30)
    each = 3.0 / 0.78
    in_a = [s for s in result.slots if s.start < at("6:00")]
    in_b = [s for s in result.slots if at("20:00") <= s.start < at("30:00")]
    assert len(in_a) + len(in_b) == len(result.slots)
    assert sum(s.grid_kwh for s in in_a) == pytest.approx(each)
    assert sum(s.grid_kwh for s in in_b) == pytest.approx(each)
    # Equal prices: the latest quarter-hours of each period, 7 full and a rest.
    assert [s.start for s in in_a][-1] == at("5:45")
    assert [s.start for s in in_b][-1] == at("29:45")
    assert min(s.start for s in in_a) == at("4:00")
    assert min(s.start for s in in_b) == at("28:00")
    assert result.energy_to_buy == pytest.approx(2 * each)


def test_a_cheaper_first_period_serves_the_second_evening_up_to_the_target() -> None:
    """A (0.04) is cheaper than B (0.05); the second evening takes from A what the
    Charge Target of 50 % (5 kWh of content) lets the battery carry through the
    first evening, and the rest from B."""
    segments = (
        ("00:00", "06:00", 0.04, True, 0.0),
        *TWO_DAYS[1:3],
        ("20:00", "30:00", 0.05, True, 0.0),
        *TWO_DAYS[4:],
    )
    result = plan_charging(T0, day(*segments), at_floor(charge_target=50.0))
    assert result.uncovered == pytest.approx(0.0, abs=1e-6)
    in_a = sum(s.grid_kwh for s in result.slots if s.start < at("6:00"))
    in_b = sum(s.grid_kwh for s in result.slots if s.start >= at("20:00"))
    assert in_a == pytest.approx(5.0 / E)
    assert in_b == pytest.approx(2 * 3.0 / 0.78 - 5.0 / E)
    assert result.energy_to_buy == pytest.approx(in_a + in_b)


# -- the planner ends -------------------------------------------------------


def test_pathological_inputs_end_with_a_plan() -> None:
    quarters = []
    for i in range(192):
        qualifies = i % 2 == 0
        quarters.append(
            Quarter(
                T0 + i * QUARTER,
                0.01 * (i % 7),
                qualifies,
                -500.0 if i % 11 == 0 else 20000.0,
                pv_w=30000.0 if i % 13 == 0 else 0.0,
                support_w=1000.0 if i % 5 == 0 else 0.0,
            )
        )
    started = time.monotonic()
    for model in (
        battery(),
        battery(charge_level=150.0, floor=120.0),
        battery(capacity_kwh=0.0),
        battery(efficiency=0.0),
        battery(efficiency=1e-9, ignore_efficiency=True),
        battery(charge_target=-10.0),
        battery(max_charge_power=1e9),
    ):
        result = plan_charging(T0, quarters, model)
        assert result.uncovered <= result.shortfall + 1e-9
        assert result.energy_to_buy >= 0.0
        assert all(s.grid_kwh > 0.0 for s in result.slots)
    assert time.monotonic() - started < 30.0


def test_many_small_shortfalls_end_with_a_plan() -> None:
    """Every second quarter-hour is a shortfall with a cheap one before it."""
    quarters = [
        Quarter(T0 + i * QUARTER, 0.05 if i % 2 == 0 else 0.5, i % 2 == 0, 100.0)
        for i in range(192)
    ]
    result = plan_charging(T0, quarters, battery(charge_level=0.0))
    assert result.uncovered < result.shortfall
    assert result.energy_to_buy <= len(result.slots) * GRID_PER_QUARTER + 1e-9


def test_planned_energy_never_exceeds_the_target_in_the_battery() -> None:
    quarters = day(
        ("00:00", "12:00", 0.05, True, 300.0),
        ("12:00", "20:00", 0.40, False, 2000.0),
    )
    result = plan_charging(T0, quarters, battery(charge_level=20.0, charge_target=70.0))
    assert sum(s.stored_kwh for s in result.slots) <= 5.0 + 1e-9


# -- discharge blocks only apply when asked for ----------------------------------


def test_without_discharge_blocks_a_qualifying_quarter_hour_drains_the_battery() -> (
    None
):
    quarters = day(
        ("00:00", "02:00", 0.05, True, 1000.0),
        ("02:00", "03:00", 0.40, False, 500.0),
    )
    held = plan_charging(
        T0, quarters, battery(charge_level=30.0, discharge_blocks=True)
    )
    drained = plan_charging(
        T0, quarters, battery(charge_level=30.0, discharge_blocks=False)
    )
    assert held.shortfall == 0.0
    assert drained.shortfall == 0.0  # the deficit is not a shortfall...
    # ... but the battery arrived at the expensive hour with less: 3 kWh minus
    # 2 kWh / e leave 0.735 kWh, which deliver 0.649 of 0.5 kWh.
    quarters_long = day(
        ("00:00", "02:00", 0.05, True, 1000.0),
        ("02:00", "04:00", 0.40, False, 500.0),
    )
    held = plan_charging(T0, quarters_long, battery(charge_level=30.0))
    drained = plan_charging(
        T0, quarters_long, battery(charge_level=30.0, discharge_blocks=False)
    )
    assert held.shortfall == 0.0
    assert drained.shortfall == pytest.approx(1.0 - (3.0 - 2.0 / E) * E)


def test_a_planned_quarter_hour_leaves_the_battery_content_unchanged_by_the_load() -> (
    None
):
    """The House's own load comes from the grid in a planned quarter-hour."""
    quarters = day(
        ("00:00", "00:15", 0.05, True, 5000.0),
        ("00:15", "01:15", 0.40, False, 500.0),
    )
    result = plan_charging(T0, quarters, at_floor())
    assert starts(result) == ["00:00"]
    assert result.slots[0].stored_kwh == pytest.approx(0.525 * E)


# -- the running quarter-hour keeps its place -------------------------------------


def running_day(first_price: float, later_price: float) -> list[Quarter]:
    return day(
        ("00:00", "00:15", first_price, True, 0.0),
        ("00:15", "01:00", later_price, True, 0.0),
        ("01:00", "02:00", 0.40, False, 300.0),
    )


def test_without_a_running_quarter_hour_the_later_equal_one_is_taken() -> None:
    result = plan_charging(T0, running_day(0.05, 0.05), at_floor())
    assert starts(result) == ["00:45"]


def test_the_running_quarter_hour_is_filled_first_on_equal_prices() -> None:
    now = T0 + timedelta(minutes=2)
    result = plan_charging(now, running_day(0.05, 0.05), at_floor(), running=T0)
    assert starts(result) == ["00:00"]
    assert result.is_due(now)


def test_a_strictly_cheaper_later_quarter_hour_does_not_replace_the_running_one() -> (
    None
):
    now = T0 + timedelta(minutes=2)
    result = plan_charging(now, running_day(0.06, 0.04), at_floor(), running=T0)
    assert starts(result)[0] == "00:00"
    # the running one is filled before the cheaper one gets anything
    assert grid_at(result, "00:00") == pytest.approx(result.slots[0].grid_kwh)
    # after the quarter-hour has ended it is the cheaper one
    later = plan_charging(T0 + QUARTER, running_day(0.06, 0.04)[1:], at_floor())
    assert starts(later) == ["00:45"]


def test_the_running_quarter_hour_is_not_kept_when_it_is_not_worth_it() -> None:
    now = T0 + timedelta(minutes=2)
    result = plan_charging(now, running_day(0.35, 0.05), at_floor(), running=T0)
    assert "00:00" not in starts(result)


def test_the_running_quarter_hour_is_not_kept_when_nothing_needs_it() -> None:
    quarters = day(
        ("00:00", "01:00", 0.05, True, 0.0),
        ("01:00", "02:00", 0.40, False, 0.0),
    )
    now = T0 + timedelta(minutes=2)
    result = plan_charging(now, quarters, at_floor(), running=T0)
    assert result.slots == ()


# -- due now -------------------------------------------------------------------


def test_charging_is_due_inside_a_planned_slot_only() -> None:
    result = real_plan()
    assert result.is_due(at("07:50"))
    assert not result.is_due(at("07:44"))
    assert not result.is_due(at("09:00"))
    assert result.is_due(at("07:45"))


def test_nothing_is_due_without_slots() -> None:
    assert not plan_charging(T0, [], battery()).is_due(T0)


# -- the Discharge Block rule ----------------------------------------------

QUALIFYING_SETS = {
    "very_cheap": {PriceLevel.VERY_CHEAP},
    "cheap_and_below": {PriceLevel.VERY_CHEAP, PriceLevel.CHEAP},
    "normal_and_below": {PriceLevel.VERY_CHEAP, PriceLevel.CHEAP, PriceLevel.NORMAL},
}
ALL_LEVELS = (
    PriceLevel.VERY_CHEAP,
    PriceLevel.CHEAP,
    PriceLevel.NORMAL,
    PriceLevel.EXPENSIVE,
    PriceLevel.VERY_EXPENSIVE,
)


@pytest.mark.parametrize("option", list(QUALIFYING_SETS))
@pytest.mark.parametrize("level", ALL_LEVELS)
def test_a_block_is_active_exactly_when_the_level_qualifies(
    option: str, level: PriceLevel
) -> None:
    qualifying = QUALIFYING_SETS[option]
    points = prices(NIGHT, [(0.3, level), (0.3, PriceLevel.VERY_EXPENSIVE)])
    now = NIGHT + timedelta(minutes=5)
    assert discharge_block_active(now, points, qualifying) is (level in qualifying)


def test_a_block_needs_a_price_for_the_instant() -> None:
    qualifying = QUALIFYING_SETS["cheap_and_below"]
    assert not discharge_block_active(NIGHT, [], qualifying)
    points = night([0.1, 0.1], level=PriceLevel.CHEAP)
    assert not discharge_block_active(NIGHT - timedelta(seconds=1), points, qualifying)
    assert not discharge_block_active(NIGHT + 2 * QUARTER, points, qualifying)


def test_a_block_follows_the_slot_boundaries_exactly() -> None:
    qualifying = QUALIFYING_SETS["cheap_and_below"]
    points = prices(
        NIGHT,
        [
            (0.4, PriceLevel.NORMAL),
            (0.1, PriceLevel.CHEAP),
            (0.4, PriceLevel.NORMAL),
        ],
    )
    second = NIGHT + QUARTER
    third = NIGHT + 2 * QUARTER
    assert not discharge_block_active(second - timedelta(seconds=1), points, qualifying)
    assert discharge_block_active(second, points, qualifying)
    assert discharge_block_active(third - timedelta(seconds=1), points, qualifying)
    assert not discharge_block_active(third, points, qualifying)


def test_a_block_does_not_depend_on_the_price_or_the_order() -> None:
    """Only the level of the point valid at the instant counts."""
    qualifying = QUALIFYING_SETS["very_cheap"]
    points = prices(NIGHT, [(0.9, PriceLevel.VERY_CHEAP), (0.01, PriceLevel.EXPENSIVE)])
    assert discharge_block_active(NIGHT, list(reversed(points)), qualifying)
    assert not discharge_block_active(NIGHT + QUARTER, points, qualifying)
