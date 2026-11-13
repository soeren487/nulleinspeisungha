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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nulleinspeisung.charging_planner import (
    BatteryModel,
    ChargingPlan,
    PlanReason,
    Quarter,
    TopUp,
    plan_charging,
    slot_length,
)

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


# -- the Discharge Block is decided by the plan (ticket 37) ----------------------


def held_at(plan: ChargingPlan) -> list[str]:
    return [h.strftime("%H:%M") for h in plan.held]


def hours(first: str, last: str) -> list[str]:
    """The quarter-hours from ``first`` up to, not including, ``last``."""
    moment, out = at(first), []
    while moment < at(last):
        out.append(moment.strftime("%H:%M"))
        moment += QUARTER
    return out


def cheap_night_dear_evening(**kw):
    """A cheap night (0.05, 300 W), a day the PV carries, a dear evening (0.256,
    500 W) that needs 2.0 kWh, which take 2.0 / e from the battery."""
    return day(
        ("00:00", "07:00", 0.05, True, 300.0),
        ("07:00", "17:00", 0.20, False, 300.0, 300.0),
        ("17:00", "21:00", 0.256, False, 500.0),
        ("21:00", "24:00", 0.05, True, 300.0),
        *kw.get("more", ()),
    )


def test_a_long_cheap_period_without_a_dearer_quarter_holds_nothing() -> None:
    """2026-11-12/13: 39 hours of cheap quarters at one price, the battery at 95 %.
    The Price Level said cheap all the time and the battery never discharged."""
    quarters = [Quarter(T0 + i * QUARTER, 0.243, True, 300.0) for i in range(156)]
    plan = plan_charging(T0, quarters, battery(charge_level=95.0))

    assert plan.held == ()
    assert not plan.is_held(T0)
    assert not plan.is_held(T0 + 20 * QUARTER)
    assert plan.slots == ()
    assert plan.reason is PlanReason.NO_SHORTFALL


def test_a_dearer_quarter_the_battery_carries_by_itself_holds_nothing() -> None:
    plan = plan_charging(
        at("00:00"), cheap_night_dear_evening(), battery(charge_level=95.0)
    )

    assert plan.held == ()
    assert plan.shortfall == 0.0
    assert plan.slots == ()


def test_before_a_dear_evening_the_latest_cheap_quarters_are_held_as_needed() -> None:
    """30 % = 3 kWh; the evening needs 2.0 / e = 2.264 kWh of it, so 0.736 kWh may
    go to the night's load (0.075 / e = 0.0849 kWh a quarter): eight quarters.
    The rest of the night, the latest quarters, are held."""
    plan = plan_charging(
        at("00:00"), cheap_night_dear_evening(), battery(charge_level=30.0)
    )

    assert held_at(plan) == hours("02:00", "07:00")
    assert plan.shortfall == 0.0
    assert plan.reference_price is None
    assert plan.slots == ()
    assert plan.reason is PlanReason.NO_SHORTFALL
    assert plan.is_held(at("02:00"))
    assert plan.is_held(at("06:59"))
    assert not plan.is_held(at("01:59"))
    assert not plan.is_held(at("07:00"))
    # The evening quarter-hours are not qualifying; the late cheap ones come after.
    assert not plan.is_held(at("21:00"))


def test_holding_comes_before_buying() -> None:
    """10 % = 1 kWh cannot carry the evening even if held all night; the rest is
    bought, and it is only what the held energy leaves."""
    plan = plan_charging(
        at("00:00"), cheap_night_dear_evening(), battery(charge_level=10.0)
    )

    missing = 2.0 - 1.0 * E
    assert plan.shortfall == pytest.approx(missing)
    assert plan.energy_to_buy == pytest.approx(missing / 0.78)
    assert plan.uncovered == pytest.approx(0.0, abs=1e-6)
    # All of the night is held (the bought quarters, at the latest, implicitly).
    assert held_at(plan) == hours("00:00", "07:00")


def test_a_shortfall_at_a_lower_price_is_no_reason_to_hold() -> None:
    quarters = day(
        ("00:00", "06:00", 0.10, True, 300.0),
        ("06:00", "07:00", 0.04, False, 500.0),
    )
    plan = plan_charging(at("00:00"), quarters, battery(charge_level=20.0))

    assert plan.shortfall > 0.0
    assert plan.held == ()


def test_a_cheaper_quarter_is_held_for_a_dearer_one_only() -> None:
    """Of two qualifying periods only the cheaper one lies below the shortfall."""
    quarters = day(
        ("00:00", "03:00", 0.30, True, 600.0),  # as dear as the shortfall
        ("03:00", "06:00", 0.05, True, 600.0),
        ("06:00", "08:00", 0.30, False, 1000.0),
    )
    plan = plan_charging(at("00:00"), quarters, battery(charge_level=50.0))

    assert plan.shortfall == pytest.approx(0.0)
    assert not any(at("00:00") <= h < at("03:00") for h in plan.held)
    assert plan.held
    assert all(at("03:00") <= h < at("06:00") for h in plan.held)


def test_without_discharge_blocks_nothing_is_held() -> None:
    plan = plan_charging(
        at("00:00"),
        cheap_night_dear_evening(),
        battery(charge_level=30.0, discharge_blocks=False),
    )

    assert plan.held == ()
    assert plan.shortfall > 0.0


def test_the_quarters_the_plan_charges_in_are_held() -> None:
    plan = plan_charging(
        at("00:00"), cheap_night_dear_evening(), battery(charge_level=10.0)
    )

    assert plan.slots
    for slot in plan.slots:
        assert plan.is_held(slot.start)


def test_held_quarters_keep_the_pv_surplus_charging_the_battery() -> None:
    """A held quarter-hour with a PV surplus charges the battery: the content at
    the evening is the same as if the surplus had been added there."""
    quarters = day(
        ("00:00", "06:00", 0.05, True, 300.0),
        ("06:00", "08:00", 0.05, True, 300.0, 1300.0),  # 1 kW surplus
        ("08:00", "10:00", 0.30, False, 1000.0),
    )
    plan = plan_charging(at("00:00"), quarters, battery(charge_level=10.0))

    # 1 kWh, the PV surplus of two hours stores 2 * e, the two dear hours need 2.
    assert plan.shortfall == pytest.approx(0.0)
    assert plan.held


def test_a_top_up_holds_the_battery_from_its_first_quarter_to_the_level() -> None:
    """The load drains the battery in the qualifying quarters; the cheapest ones
    are not next to each other. From the first planned quarter-hour to the one
    that reaches 95 % the battery is held so that it does not give back what was
    bought; before and after it is not."""
    quarters = day(
        ("10:00", "13:00", 0.30, False, 0.0),
        ("13:00", "14:00", 0.30, True, 600.0),
        ("14:00", "14:30", 0.05, True, 600.0),
        ("14:30", "15:30", 0.20, True, 600.0),
        ("15:30", "16:00", 0.05, True, 600.0),
        ("16:00", "17:00", 0.30, True, 600.0),
        ("17:00", "24:00", 0.30, False, 0.0),
    )
    plan = top_up_plan(quarters)

    assert starts(plan) == ["14:00", "14:15", "15:15", "15:30", "15:45"]
    assert held_at(plan) == hours("14:00", "16:00")
    assert not plan.is_held(at("13:59"))
    assert not plan.is_held(at("16:00"))
    # The 0.1 kWh above the floor went to the load before the first quarter
    # (13:00 is not held); from there on nothing is given back: 2.0 kWh stored.
    assert plan.top_up_kwh == pytest.approx(2.0 / E)


def test_a_top_up_that_is_not_wanted_holds_nothing() -> None:
    plan = top_up_plan(
        day(("10:00", "24:00", 0.10, True, 600.0)), top_up=None, charge_level=95.0
    )

    assert plan.held == ()


def test_a_top_up_limits_its_hold_to_its_window() -> None:
    """Qualifying quarters after the end of the window are not held for it."""
    quarters = day(
        ("10:00", "23:00", 0.30, False, 0.0),
        ("23:00", "24:00", 0.05, True, 600.0),
        ("24:00", "26:00", 0.30, True, 600.0),
    )
    plan = top_up_plan(quarters)

    assert plan.slots
    assert all(h < at("24:00") for h in plan.held)


def test_the_buffer_holds_in_its_own_runs_and_the_plan_stays_consistent() -> None:
    """With the Safety Buffer the pessimistic runs hold the same way: the plan of
    the cheap night before a dear evening is unchanged by a buffer that needs
    nothing."""
    quarters = cheap_night_dear_evening()
    without = plan_charging(at("00:00"), quarters, battery(charge_level=30.0))
    with_buffer = plan_charging(
        at("00:00"), quarters, battery(charge_level=30.0), buffer=0.5
    )

    assert with_buffer.held == without.held
    assert with_buffer.slots == without.slots
    assert with_buffer.buffer_kwh == 0.0


def test_a_planning_run_over_48_hours_with_many_shortfalls_is_quick() -> None:
    quarters = [
        Quarter(
            T0 + i * QUARTER,
            0.05 if (i // 16) % 2 == 0 else 0.30,
            (i // 16) % 2 == 0,
            400.0,
        )
        for i in range(192)
    ]
    started = time.monotonic()
    plan = plan_charging(T0, quarters, battery(charge_level=60.0), buffer=0.5)

    assert time.monotonic() - started < 5.0
    assert plan.uncovered <= plan.shortfall + 1e-9


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


# -- the BatteryLife Top-up --------------------------------------------------

TOP_UP = TopUp(level=95.0, end=at("24:00"))
"""Until midnight of the test day, as the House asks for it."""
TOP_UP_GRID = (2.0 - 0.1) / E
"""Level 76 % with the minimum at 75 % holds 0.1 kWh; 95 % holds 2.0 kWh."""


def top_up_plan(quarters: list[Quarter], top_up: TopUp | None = TOP_UP, **kw):
    """10:00, 76 % with the Victron's minimum at 75 %, and a top-up unless told."""
    settings = {"charge_level": 76.0, "floor": 75.0} | kw
    return plan_charging(
        at("10:00"), quarters, battery(**settings), sunrise=None, top_up=top_up
    )


def top_up_day(cheap: float = 0.10, after: float = 0.01, load: float = 0.0, **kw):
    """Dear, non-qualifying until 14:00; ``cheap`` from 14:00 to 16:00; dear again;
    the evening 17:00 to 21:00 at ``load``; and a very cheap night after midnight."""
    return day(
        ("10:00", "14:00", 0.30, False, 0.0, kw.get("pv", 0.0)),
        ("14:00", "16:00", cheap, True, 0.0),
        ("16:00", "17:00", 0.30, False, 0.0),
        ("17:00", "21:00", 0.256, False, load),
        ("21:00", "24:00", 0.30, False, 0.0),
        ("24:00", "26:00", after, True, 0.0),
    )


def test_the_top_up_buys_in_the_cheapest_quarters_before_midnight_up_to_95() -> None:
    plan = top_up_plan(top_up_day())

    assert plan.reason is PlanReason.CHARGING_PLANNED
    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID)
    assert plan.energy_to_buy == pytest.approx(TOP_UP_GRID)
    # On equal prices the later quarter-hour: 15:45 backwards.
    assert starts(plan) == ["14:45", "15:00", "15:15", "15:30", "15:45"]
    assert all(s.price == 0.10 for s in plan.slots)
    assert plan.shortfall == 0.0
    assert plan.uncovered == 0.0


def test_the_top_up_charges_up_to_the_level_it_is_asked_for() -> None:
    """Above the floor of 75 % the battery holds 2.5 kWh; 76 % are 0.1 kWh of it."""
    full = top_up_plan(top_up_day(), top_up=TopUp(level=100.0, end=at("24:00")))
    ninety = top_up_plan(top_up_day(), top_up=TopUp(level=90.0, end=at("24:00")))

    assert full.top_up_kwh == pytest.approx(2.4 / E)
    assert ninety.top_up_kwh == pytest.approx(1.4 / E)
    assert sum(s.stored_kwh for s in full.slots) == pytest.approx(2.4)


def test_a_charge_target_below_the_level_rules_the_top_up_out() -> None:
    plan = top_up_plan(
        top_up_day(),
        top_up=TopUp(level=100.0, end=at("24:00")),
        charge_target=99.0,
    )

    assert plan.slots == ()


def test_quarters_after_the_end_of_the_window_are_not_used_for_the_top_up() -> None:
    plan = top_up_plan(top_up_day(after=0.001))

    assert plan.slots
    assert all(s.start < at("24:00") for s in plan.slots)


def test_the_top_up_may_end_at_a_cheaper_price_than_a_shortfall_would_pay() -> None:
    """No efficiency test: 0.50 qualifies for the top-up though the shortfall
    price is 0.256 and 0.50 / 0.78 is far above it."""
    quarters = top_up_day(cheap=0.50, load=300.0)

    without = top_up_plan(quarters, top_up=None)
    with_top_up = top_up_plan(quarters)

    assert without.slots == ()
    assert without.reason is PlanReason.BLOCKED_BY_EFFICIENCY
    assert with_top_up.top_up_kwh == pytest.approx(TOP_UP_GRID)
    assert all(s.price == 0.50 for s in with_top_up.slots)


def test_nothing_is_bought_when_95_is_already_reached() -> None:
    plan = top_up_plan(top_up_day(), charge_level=95.0)

    assert plan.slots == ()
    assert plan.top_up_kwh == 0.0
    assert plan.reason is PlanReason.NO_SHORTFALL


def test_nothing_is_bought_when_the_counted_pv_forecast_reaches_95() -> None:
    plan = top_up_plan(top_up_day(pv=4000.0))

    assert plan.slots == ()
    assert plan.top_up_kwh == 0.0


def test_a_little_pv_lowers_what_the_top_up_buys() -> None:
    """100 W for four hours are 0.4 kWh at the meter, stored as 0.35 kWh."""
    plan = top_up_plan(top_up_day(pv=100.0))

    assert 0.0 < plan.top_up_kwh < TOP_UP_GRID
    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID - 0.4)


def test_nothing_is_bought_when_no_qualifying_quarter_is_left_before_midnight() -> None:
    quarters = day(
        ("10:00", "24:00", 0.30, False, 0.0),
        ("24:00", "26:00", 0.01, True, 0.0),
    )

    plan = top_up_plan(quarters)

    assert plan.slots == ()
    assert plan.top_up_kwh == 0.0


def test_a_charge_target_below_95_rules_the_top_up_out() -> None:
    plan = top_up_plan(top_up_day(), charge_target=90.0)

    assert plan.slots == ()
    assert plan.top_up_kwh == 0.0


def test_a_charge_target_of_exactly_95_allows_the_top_up() -> None:
    plan = top_up_plan(top_up_day(), charge_target=95.0)

    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID)


def test_the_maximum_charge_power_limits_every_quarter_of_the_top_up() -> None:
    quarters = day(
        ("10:00", "20:00", 0.10, True, 0.0),
        ("20:00", "24:00", 0.30, False, 0.0),
    )

    plan = top_up_plan(quarters, max_charge_power=1000.0)

    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID)
    assert all(s.grid_kwh <= 0.25 + 1e-9 for s in plan.slots)
    assert len(plan.slots) == math.ceil(TOP_UP_GRID / 0.25)


def test_the_top_up_buys_what_it_can_when_the_window_is_too_short() -> None:
    quarters = day(
        ("10:00", "14:00", 0.30, False, 0.0),
        ("14:00", "14:30", 0.10, True, 0.0),
        ("14:30", "24:00", 0.30, False, 0.0),
    )

    plan = top_up_plan(quarters)

    assert plan.top_up_kwh == pytest.approx(2 * GRID_PER_QUARTER)
    assert plan.reason is PlanReason.CHARGING_PLANNED


def test_the_shortfall_planning_sees_the_energy_the_top_up_stores() -> None:
    """The evening needs 1.2 kWh; 2.0 kWh in the battery carry it, so the top-up
    covers the shortfall and nothing is bought on top."""
    plan = top_up_plan(top_up_day(load=300.0))

    assert plan.shortfall > 1.0  # measured without any charging
    assert plan.reference_price == pytest.approx(0.256)
    assert plan.uncovered == 0.0
    assert plan.energy_to_buy == pytest.approx(plan.top_up_kwh)
    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID)


def test_a_shortfall_the_top_up_leaves_is_still_covered_on_top() -> None:
    """The evening needs 2.0 kWh; the 2.0 kWh stored deliver only 1.77."""
    plan = top_up_plan(top_up_day(cheap=0.05, load=500.0), ignore_efficiency=True)

    assert plan.top_up_kwh == pytest.approx(TOP_UP_GRID)
    assert plan.energy_to_buy > plan.top_up_kwh + 0.1
    assert plan.uncovered == pytest.approx(0.0, abs=1e-6)


def test_without_a_top_up_the_plan_is_what_it_was() -> None:
    plan = top_up_plan(top_up_day(load=300.0), top_up=None)

    assert plan.top_up_kwh == 0.0
    assert plan.energy_to_buy == pytest.approx((1.2 - 0.1 * E) / 0.78)


# -- the Safety Buffer -------------------------------------------------------


def buffer_day(
    qualifying_until: str, evening: float = 0.0, pv_w: float = 4000.0
) -> list[Quarter]:
    """10:00 to midnight: 0.10 and qualifying until ``qualifying_until``, 0.30 and
    not after it. 4 kW of counted PV from 12:00 to 14:00; ``evening`` W of load from
    17:00 to 21:00."""
    quarters = []
    moment = at("10:00")
    while moment < at("24:00"):
        qualifies = moment < at(qualifying_until)
        pv = pv_w if at("12:00") <= moment < at("14:00") else 0.0
        load = evening if at("17:00") <= moment < at("21:00") else 0.0
        quarters.append(
            Quarter(moment, 0.10 if qualifies else 0.30, qualifies, load, pv)
        )
        moment += QUARTER
    return quarters


def buffer_top_up(qualifying_until: str, buffer: float | None = 0.5, **kw):
    """10:00, 87 % (8.7 of 10 kWh): 0.8 kWh stored, 0.906 kWh bought, reach 95 %."""
    settings = {"charge_level": 87.0} | kw
    return plan_charging(
        at("10:00"),
        buffer_day(qualifying_until),
        battery(**settings),
        top_up=TOP_UP,
        buffer=buffer,
    )


def buffer_shortfall(qualifying_until: str, buffer: float | None = 0.5, **kw):
    """10:00, empty battery, 500 W of load from 17:00 to 21:00 at a dear price and
    no counted PV: the 2.0 kWh it needs take 2.564 kWh bought, 9.77 quarter-hours
    at half power."""
    settings = {"charge_level": 0.0} | kw
    return plan_charging(
        at("10:00"),
        buffer_day(qualifying_until, evening=500.0, pv_w=0.0),
        battery(**settings),
        buffer=buffer,
    )


def test_with_plenty_of_time_left_the_sun_is_trusted_for_now() -> None:
    """The owner's 2026-10-25: 13 h of the same price ahead, the PV fills the rest."""
    result = buffer_top_up("23:00")

    assert at("10:00") not in [s.start for s in result.slots]
    assert result.buffer_kwh == 0.0
    # Without the sun the top-up is six half-power quarter-hours at the very end.
    assert result.energy_to_buy == pytest.approx(0.0)


def test_with_only_just_enough_quarters_left_the_current_one_is_charged() -> None:
    """Four quarter-hours at half power without PV are just enough (3.45 needed)."""
    result = buffer_top_up("11:00")

    assert starts(result) == ["10:00"]
    assert result.buffer_kwh > 0.0
    assert result.reason is PlanReason.CHARGING_PLANNED
    # The forced energy is the top-up's: the pessimistic top-up used the quarter.
    assert result.top_up_kwh == pytest.approx(result.buffer_kwh)
    # Only the current quarter is taken over: the sun still has the rest.
    assert result.energy_to_buy == pytest.approx(result.buffer_kwh)


def test_one_quarter_more_than_needed_and_the_plan_is_the_normal_one() -> None:
    result = buffer_top_up("11:15")

    assert result.slots == ()
    assert result.buffer_kwh == 0.0


def test_without_a_buffer_the_plan_is_the_old_one() -> None:
    result = buffer_top_up("11:00", buffer=None)

    assert result.slots == ()
    assert result.buffer_kwh == 0.0
    assert result == plan_charging(
        at("10:00"),
        buffer_day("11:00"),
        battery(charge_level=87.0),
        top_up=TOP_UP,
    )


def test_a_buffer_of_zero_assumes_no_sun_at_full_power() -> None:
    """Full power: 0.906 kWh take 1.73 quarter-hours."""
    assert starts(buffer_top_up("10:30", buffer=0.0)) == ["10:00"]
    assert buffer_top_up("10:45", buffer=0.0).slots == ()
    # The same three quarter-hours do not suffice at half power.
    assert starts(buffer_top_up("10:45", buffer=0.5)) == ["10:00"]


def test_a_shortfall_is_treated_like_the_top_up() -> None:
    forced = buffer_shortfall("12:30")
    assert "10:00" in starts(forced)
    assert forced.buffer_kwh > 0.0
    # No top-up asked for: the energy is the shortfall's.
    assert forced.top_up_kwh == 0.0

    relaxed = buffer_shortfall("12:45")
    assert "10:00" not in starts(relaxed)
    assert relaxed.buffer_kwh == 0.0


def test_the_efficiency_test_still_stops_the_pessimistic_shortfall_cover() -> None:
    """0.25 divided by the shortfall's 0.30 is above the efficiency of 0.78."""
    quarters = [
        replace(q, price=0.25) if q.qualifies else q
        for q in buffer_day("12:30", 500.0, pv_w=0.0)
    ]
    blocked = plan_charging(
        at("10:00"), quarters, battery(charge_level=0.0), buffer=0.5
    )
    assert blocked.slots == ()
    assert blocked.buffer_kwh == 0.0

    # Skipping the test makes the same day force the current quarter.
    ignoring = plan_charging(
        at("10:00"),
        quarters,
        battery(charge_level=0.0, ignore_efficiency=True),
        buffer=0.5,
    )
    assert "10:00" in starts(ignoring)


def test_the_charge_target_limits_what_the_buffer_forces() -> None:
    """Target 20 % (2.0 kWh): 8 quarter-hours at half power cannot reach it, so
    all are needed, and still nothing is stored above the target."""
    result = buffer_shortfall("12:00", charge_target=20.0)

    assert "10:00" in starts(result)
    assert result.buffer_kwh > 0.0
    assert result.energy_to_buy * E <= 2.0 + 1e-6


def test_a_quarter_hour_charged_in_stays_in_the_plan_while_the_buffer_needs_it() -> (
    None
):
    """A replan five minutes into the quarter-hour does not drop it."""
    result = plan_charging(
        at("10:00") + timedelta(minutes=5),
        buffer_day("11:00"),
        battery(charge_level=87.0),
        running=at("10:00"),
        top_up=TOP_UP,
        buffer=0.5,
    )

    assert starts(result) == ["10:00"]
    assert result.buffer_kwh > 0.0


def sunny_day_after_a_cheap_night(pv_w: float) -> list[Quarter]:
    """02:00 to midnight: cheap qualifying night until 07:00, then dear; 800 W of
    load from 07:00 to 21:00 and ``pv_w`` of counted PV from 08:00 to 17:00."""
    quarters = []
    moment = at("02:00")
    while moment < at("24:00"):
        night = moment < at("07:00")
        day_load = at("07:00") <= moment < at("21:00")
        sun = at("08:00") <= moment < at("17:00")
        quarters.append(
            Quarter(
                moment,
                0.10 if night else 0.30,
                night,
                800.0 if day_load else 0.0,
                pv_w if sun else 0.0,
            )
        )
        moment += QUARTER
    return quarters


def test_a_cheap_night_before_a_sunny_day_is_not_charged_in_for_the_shortfalls() -> (
    None
):
    """The counted forecast covers the day, so the shortfalls need nothing; the
    buffer is a time margin there, not a bet against the sun."""
    result = plan_charging(
        at("02:00"),
        sunny_day_after_a_cheap_night(1500.0),
        battery(charge_level=50.0),
        buffer=0.5,
    )
    assert result.slots == ()
    assert result.buffer_kwh == 0.0

    # Had the shortfalls been planned without PV, the night would be needed.
    no_pv = plan_charging(
        at("02:00"),
        sunny_day_after_a_cheap_night(0.0),
        battery(charge_level=50.0),
        buffer=0.5,
    )
    assert at("02:00") in [s.start for s in no_pv.slots]


# -- the Safety Buffer charges steadily (ticket 36) ---------------------------

LIVING_LEVEL = 95.0 - 2.4 * E / 10.0 * 100.0
"""2026-11-10, 21:30: the top-up to 95 % needs 2.4 kWh from the grid."""
OFFICE_LEVEL = 95.0 - 4.1 * E / 10.0 * 100.0


def night_plan(
    now: datetime,
    level: float,
    window_end: str = "24:00",
    qualifying_until: str = "24:00",
    latched: bool = False,
    **kw,
) -> ChargingPlan:
    """The top-up of the evening of 2026-11-10 with the buffer at 50 %: the
    quarter-hours up to ``qualifying_until`` qualify (0.10), the ones to 02:00 do
    not (0.30), no load (what the battery holds then does not move; a load that
    drains it before the charging starts is tested with the holding); the window
    ends at ``window_end``."""
    quarters = []
    moment = now - timedelta(minutes=now.minute % 15)
    while moment < at("26:00"):
        qualifies = moment < at(qualifying_until)
        quarters.append(Quarter(moment, 0.10 if qualifies else 0.30, qualifies, 0.0))
        moment += QUARTER
    return plan_charging(
        now,
        quarters,
        battery(charge_level=level, **kw),
        top_up=TopUp(95.0, at(window_end)),
        buffer=0.5,
        buffer_latched=latched,
    )


def charge_through(level: float, **kw) -> tuple[list[ChargingPlan], float]:
    """Plan every quarter-hour from 21:30 to midnight as the House would: the
    battery gains what the quarter's plan charges, and the buffer is latched when
    it charged in the quarter before. The plans and the final level."""
    plans = []
    latched = False
    now = at("21:30")
    while now < at("24:00"):
        plan = night_plan(now, level, latched=latched, **kw)
        plans.append(plan)
        if plan.slots and plan.slots[0].start == now:
            level += plan.slots[0].grid_kwh * E / 10.0 * 100.0
        latched = plan.buffer_kwh > 0.0
        now += QUARTER
    return plans, level


def test_the_buffer_charges_steadily_below_the_full_power_in_every_quarter() -> None:
    """Ten quarter-hours, 2.4 kWh: at half power they would just do, so the buffer
    starts now; it must not charge at full power now and pause next quarter."""
    first = night_plan(at("21:30"), LIVING_LEVEL)

    assert first.buffer_kwh > 0.0
    assert first.buffer_power is not None
    assert 500.0 < first.buffer_power < 2100.0
    # 2.4 kWh in 2.5 h is 960 W; at half the time 1920 W.
    assert first.buffer_power == pytest.approx(2.4 / 2.5 * 1000.0 / 0.5)
    assert grid_at(first, "21:30") == pytest.approx(first.buffer_power * 0.25 / 1000.0)


def test_the_latch_keeps_the_buffer_charging_in_the_next_quarters() -> None:
    plans, level = charge_through(LIVING_LEVEL)

    # Every quarter-hour charges, until the top-up is reached.
    reached = next(i for i, p in enumerate(plans) if not p.slots)
    assert reached >= 5
    for i, plan in enumerate(plans[:reached]):
        assert plan.slots[0].start == at("21:30") + QUARTER * i
        assert plan.buffer_power is not None
    assert all(not p.slots for p in plans[reached:])
    assert level >= 95.0 - 0.01
    # The power follows the need: it never rises, never leaves the limits.
    powers = [p.buffer_power for p in plans[:reached]]
    assert powers == sorted(powers, reverse=True)
    assert all(500.0 <= p <= 2100.0 for p in powers)


def test_without_the_latch_the_buffer_would_pause() -> None:
    """What the ticket saw: the energy stored makes the next quarter unnecessary."""
    first = night_plan(at("21:30"), LIVING_LEVEL)
    level = LIVING_LEVEL + first.slots[0].grid_kwh * E / 10.0 * 100.0

    assert at("21:45") not in [s.start for s in night_plan(at("21:45"), level).slots]
    latched = night_plan(at("21:45"), level, latched=True)
    assert latched.slots[0].start == at("21:45")
    assert latched.buffer_power is not None


def test_the_latch_needs_an_open_need_and_a_qualifying_quarter() -> None:
    reached = night_plan(at("22:00"), 95.0, latched=True)
    assert reached.slots == ()
    assert reached.buffer_power is None

    # The window is still open, the quarter-hour is not qualifying.
    quarters = [
        Quarter(at("22:00") + QUARTER * i, 0.30 if i == 0 else 0.10, i > 0, 300.0)
        for i in range(8)
    ]
    plan = plan_charging(
        at("22:00"),
        quarters,
        battery(charge_level=LIVING_LEVEL),
        top_up=TopUp(95.0, at("24:00")),
        buffer=0.5,
        buffer_latched=True,
    )
    assert plan.buffer_power is None
    assert at("22:00") not in [s.start for s in plan.slots]


def test_the_office_house_charges_at_the_full_power_continuously() -> None:
    """4.1 kWh in ten quarter-hours is more than half power can do."""
    plans, level = charge_through(OFFICE_LEVEL)

    assert plans[0].buffer_power == pytest.approx(2100.0)
    full = [p for p in plans if p.buffer_power is not None]
    assert len(full) >= 8
    for i, plan in enumerate(full):
        assert plan.slots[0].start == at("21:30") + QUARTER * i
    for plan in full[:6]:
        assert plan.slots[0].grid_kwh == pytest.approx(GRID_PER_QUARTER)
    assert level >= 95.0 - 0.01


def test_qualifying_quarters_after_midnight_lower_the_power() -> None:
    """The window reaches to 02:00 when the prices go on qualifying: the same need
    is spread over more hours."""
    midnight = night_plan(at("21:30"), LIVING_LEVEL, latched=True)
    later = night_plan(
        at("21:30"),
        LIVING_LEVEL,
        window_end="26:00",
        qualifying_until="26:00",
        latched=True,
    )

    assert later.buffer_power is not None
    assert midnight.buffer_power is not None
    assert later.buffer_power < midnight.buffer_power
    # 2.4 kWh in 4.5 h is 533 W, at half the time 1067 W.
    assert later.buffer_power == pytest.approx(2.4 / 4.5 * 1000.0 / 0.5)


def test_the_buffer_never_charges_below_500_w() -> None:
    """A small need at the start of a long window."""
    plan = night_plan(
        at("21:30"),
        95.0 - 0.3 * E / 10.0 * 100.0,
        window_end="26:00",
        qualifying_until="26:00",
        latched=True,
    )

    assert plan.buffer_power == pytest.approx(500.0)
    assert grid_at(plan, "21:30") == pytest.approx(0.125)


def test_the_buffer_never_charges_above_the_maximum_charge_power() -> None:
    plan = night_plan(at("21:30"), OFFICE_LEVEL, max_charge_power=1500.0)

    assert plan.buffer_power == pytest.approx(1500.0)
    assert grid_at(plan, "21:30") == pytest.approx(1500.0 * 0.25 / 1000.0)


def test_a_part_of_the_current_quarter_counts_with_what_remains_of_it() -> None:
    plan = night_plan(at("21:30") + timedelta(minutes=5), LIVING_LEVEL)

    assert plan.buffer_power is not None
    # 2.4 kWh in 2.33 h, at half the time.
    assert plan.buffer_power == pytest.approx(2.4 / (2.5 - 5 / 60) * 1000.0 / 0.5)
    assert grid_at(plan, "21:30") == pytest.approx(
        plan.buffer_power * (10 / 60) / 1000.0
    )


def test_the_charge_target_limits_the_steady_buffer_too() -> None:
    plan = night_plan(at("21:30"), OFFICE_LEVEL, charge_target=80.0)

    # 80 % hold 8.0 of 10 kWh; the level is OFFICE_LEVEL = 95 - 3.62 = 91.4 % ...
    # below the target nothing is charged at all.
    assert plan.slots == ()
    assert plan.buffer_power is None


def test_without_a_buffer_there_is_no_buffer_power() -> None:
    quarters = [
        Quarter(at("21:30") + QUARTER * i, 0.10, True, 300.0) for i in range(10)
    ]
    plan = plan_charging(
        at("21:30"),
        quarters,
        battery(charge_level=LIVING_LEVEL),
        top_up=TopUp(95.0, at("24:00")),
        buffer=None,
        buffer_latched=True,
    )

    assert plan.buffer_power is None
    assert plan.buffer_kwh == 0.0
