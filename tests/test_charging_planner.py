"""The Charging Plan, tested directly: no Home Assistant, the time is passed in."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.nulleinspeisung.charging_planner import (
    ChargingPlan,
    PlanReason,
    plan_charging,
    slot_length,
)

NIGHT = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
QUARTER = timedelta(minutes=15)
CHEAP = {"very_cheap", "cheap"}


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


def night(
    values: list[float], level: str = "cheap", start: datetime = NIGHT
) -> list[Price]:
    return prices(start, [(v, level) for v in values])


def plan(
    price_list: list[Price],
    *,
    now: datetime = NIGHT,
    qualifying: set[str] = CHEAP,
    level: float = 50.0,
    capacity: float = 10.0,
    target: float = 100.0,
    power: float = 2000.0,
    deadline: datetime = DEADLINE,
    efficiency: float = 1.0,
    ignore: bool = False,
    max_energy: float | None = None,
) -> ChargingPlan:
    return plan_charging(
        now,
        price_list,
        qualifying,
        level,
        capacity,
        target,
        power,
        deadline,
        efficiency,
        ignore,
        max_energy,
    )


def starts(result: ChargingPlan) -> list[datetime]:
    return [s.start for s in result.slots]


# -- picking -----------------------------------------------------------------


def test_the_cheapest_slots_are_picked_and_sorted_by_start() -> None:
    """Five kWh at 0.5 kWh a slot: the ten cheapest of 32, in time order."""
    values = [0.30 + 0.01 * ((i * 7) % 32) for i in range(32)]  # all different
    result = plan(night(values))
    assert result.reason is PlanReason.CHARGING_PLANNED
    assert len(result.slots) == 10
    cheapest = sorted(range(32), key=lambda i: values[i])[:10]
    assert starts(result) == [NIGHT + QUARTER * i for i in sorted(cheapest)]
    assert starts(result) == sorted(starts(result))
    assert result.energy_missing == pytest.approx(5.0)
    assert result.energy_to_buy == pytest.approx(5.0)
    assert result.reachable


def test_the_current_slot_counts_with_what_remains_of_it() -> None:
    """Ten minutes into the cheapest slot only 10 of its 15 minutes are left."""
    values = [0.30] * 32
    values[0] = 0.10
    now = NIGHT + timedelta(minutes=5)
    result = plan(night(values), now=now, level=92.0, capacity=10.0)
    # 0.8 kWh missing: the current slot gives 2000 W * 10 min = 0.3333 kWh.
    assert result.slots[0].start == NIGHT
    assert result.slots[0].stored_kwh == pytest.approx(2.0 * 10 / 60)
    assert result.slots[0].drawn_kwh == pytest.approx(2.0 * 10 / 60)
    assert len(result.slots) == 2
    assert result.energy_to_buy == pytest.approx(0.8)


def test_a_slot_that_ended_is_not_in_the_plan() -> None:
    """The slot before now is the cheapest, and still not picked."""
    values = [0.01] + [0.30] * 31
    now = NIGHT + QUARTER
    result = plan(night(values), now=now, level=95.0)
    assert NIGHT not in starts(result)


def test_nothing_at_or_after_the_deadline_is_planned() -> None:
    """The cheapest slots start at the deadline or later: they stay out."""
    values = [0.30] * 32 + [0.01] * 8  # from the deadline on: very cheap
    result = plan(night(values), ignore=True, capacity=1000.0, level=0.0)
    assert len(result.slots) == 32
    assert result.slots
    assert all(s.start < DEADLINE for s in result.slots)
    assert max(starts(result)) == DEADLINE - QUARTER


def test_the_last_slot_before_the_deadline_may_be_planned() -> None:
    """A slot starting at deadline minus one slot is in; it may end after."""
    deadline = DEADLINE - timedelta(minutes=5)
    result = plan(
        night([0.3] * 32), deadline=deadline, level=0.0, capacity=1000.0, ignore=True
    )
    assert len(result.slots) == 32
    assert max(starts(result)) == DEADLINE - QUARTER


def test_ties_are_broken_by_the_earlier_slot() -> None:
    result = plan(night([0.2] * 32), level=90.0)  # 1 kWh: two slots
    assert starts(result) == [NIGHT, NIGHT + QUARTER]


def test_the_last_slot_may_overshoot_but_the_energy_to_buy_is_capped() -> None:
    """0.7 kWh missing needs two slots of 0.5; only 0.7 kWh is bought."""
    result = plan(night([0.2] * 32), level=93.0)
    assert len(result.slots) == 2
    assert sum(s.stored_kwh for s in result.slots) == pytest.approx(1.0)
    assert result.energy_missing == pytest.approx(0.7)
    assert result.energy_to_buy == pytest.approx(0.7)
    assert result.reachable


def test_a_target_out_of_reach_is_planned_as_far_as_it_goes() -> None:
    """Only four slots exist: they are all used, and the target is not reachable."""
    result = plan(night([0.2] * 4))
    assert len(result.slots) == 4
    assert not result.reachable
    assert result.energy_to_buy == pytest.approx(2.0)
    assert result.energy_missing == pytest.approx(5.0)
    assert result.reason is PlanReason.CHARGING_PLANNED


# -- which slots qualify -------------------------------------------------------


def test_only_the_qualifying_levels_are_candidates() -> None:
    cheap_but_dear = prices(
        NIGHT,
        [(0.30, "very_cheap"), (0.10, "normal"), (0.20, "cheap"), (0.05, "expensive")]
        * 8,
    )
    narrow = plan(cheap_but_dear, qualifying={"very_cheap"}, level=0.0, ignore=True)
    assert {s.level for s in narrow.slots} == {"very_cheap"}
    assert len(narrow.slots) == 8
    wide = plan(
        cheap_but_dear,
        qualifying={"very_cheap", "cheap", "normal"},
        level=0.0,
        ignore=True,
    )
    assert {s.level for s in wide.slots} == {"very_cheap", "cheap", "normal"}
    assert len(wide.slots) == 20  # 24 qualify, 20 slots cover 10 kWh


def test_no_slot_of_a_qualifying_level_means_no_qualifying_slot() -> None:
    result = plan(night([0.2] * 32, level="normal"), qualifying={"very_cheap"})
    assert result.reason is PlanReason.NO_QUALIFYING_SLOT
    assert result.slots == ()
    assert result.energy_to_buy == 0.0
    assert result.energy_missing == pytest.approx(5.0)
    assert not result.reachable


# -- no work, no prices ---------------------------------------------------------


def test_a_full_battery_has_reached_the_target() -> None:
    result = plan(night([0.2] * 32), level=100.0)
    assert result.reason is PlanReason.TARGET_REACHED
    assert result.slots == ()
    assert result.energy_missing == 0.0
    assert result.energy_to_buy == 0.0
    assert result.reachable


def test_a_level_above_a_lower_target_has_reached_it() -> None:
    result = plan(night([0.2] * 32), level=85.0, target=80.0)
    assert result.reason is PlanReason.TARGET_REACHED
    assert result.energy_missing == 0.0


def test_a_target_of_80_percent_asks_for_the_energy_to_80_percent() -> None:
    result = plan(night([0.2] * 32), level=50.0, target=80.0)
    assert result.energy_missing == pytest.approx(3.0)
    assert len(result.slots) == 6


def test_without_any_price_nothing_is_planned() -> None:
    result = plan([])
    assert result.reason is PlanReason.NO_PRICES
    assert result.slots == ()
    assert result.reference_price is None
    assert result.energy_missing == pytest.approx(5.0)


def test_prices_all_in_the_past_are_no_prices() -> None:
    before = night([0.1] * 8, start=NIGHT - timedelta(hours=5))
    assert plan(before).reason is PlanReason.NO_PRICES


def test_prices_only_after_the_deadline_are_no_prices() -> None:
    after = night([0.1] * 8, start=DEADLINE)
    assert plan(after).reason is PlanReason.NO_PRICES


def test_prices_that_cover_part_of_the_night_are_used() -> None:
    result = plan(night([0.2] * 8), level=80.0)  # 2 kWh = 4 slots, 8 known
    assert result.reason is PlanReason.CHARGING_PLANNED
    assert len(result.slots) == 4


def test_a_target_reached_battery_beats_missing_prices() -> None:
    assert plan([], level=100.0).reason is PlanReason.TARGET_REACHED


# -- the worth test -------------------------------------------------------------

DAY = DEADLINE  # the Reference Price looks at the day from the deadline on


def with_reference(night_values: list[float], reference: float) -> list[Price]:
    """A night of cheap slots followed by a day of normal slots at ``reference``."""
    return night(night_values) + prices(DAY, [(reference, "normal")] * 96)


def test_the_reference_price_is_the_mean_of_non_qualifying_slots_after_it() -> None:
    day = prices(DAY, [(0.20, "normal"), (0.40, "expensive"), (0.05, "cheap")] * 8)
    result = plan(night([0.01] * 32) + day, efficiency=0.5)
    assert result.reference_price == pytest.approx(0.30)  # the cheap one is left out


def test_slots_before_the_deadline_do_not_enter_the_reference_price() -> None:
    mixed = prices(NIGHT, [(0.90, "expensive")] * 4 + [(0.10, "cheap")] * 28)
    day = prices(DAY, [(0.40, "normal")] * 4)
    assert plan(mixed + day).reference_price == pytest.approx(0.40)


def test_the_reference_price_stops_24_hours_after_the_deadline() -> None:
    inside = prices(DAY, [(0.40, "normal")] * 96)  # up to deadline + 24 h
    beyond = prices(DAY + timedelta(hours=24), [(9.0, "normal")] * 8)
    result = plan(night([0.1] * 32) + inside + beyond)
    assert result.reference_price == pytest.approx(0.40)


def test_the_slot_starting_at_the_deadline_counts_for_the_reference_price() -> None:
    result = plan(night([0.1] * 32) + prices(DAY, [(0.55, "normal")]))
    assert result.reference_price == pytest.approx(0.55)


def test_without_a_reference_price_the_worth_test_is_skipped() -> None:
    """No known slot after the deadline: even a dear slot is charged in."""
    result = plan(night([5.0] * 32, level="cheap"), efficiency=0.5)
    assert result.reference_price is None
    assert result.reason is PlanReason.CHARGING_PLANNED


def test_only_qualifying_slots_after_the_deadline_give_no_reference_price() -> None:
    result = plan(night([0.1] * 32) + prices(DAY, [(0.1, "cheap")] * 8))
    assert result.reference_price is None


def test_a_slot_at_exactly_efficiency_times_reference_passes() -> None:
    """0.8 * 0.35 is 0.27999999999999997 in floats; 0.28 still passes."""
    result = plan(with_reference([0.28] * 32, 0.35), efficiency=0.8)
    assert result.reason is PlanReason.CHARGING_PLANNED
    assert result.reference_price == pytest.approx(0.35)


def test_a_slot_just_above_the_threshold_does_not_pass() -> None:
    result = plan(with_reference([0.2801] * 32, 0.35), efficiency=0.8)
    assert result.reason is PlanReason.BLOCKED_BY_EFFICIENCY


def test_only_the_slots_that_pass_are_charged_in() -> None:
    """The cheap-by-level slots above the threshold are left out."""
    values = [0.20, 0.30, 0.25, 0.40] * 8
    result = plan(with_reference(values, 0.40), efficiency=0.78, level=0.0)
    assert result.reason is PlanReason.CHARGING_PLANNED
    assert all(s.price <= 0.78 * 0.40 + 1e-9 for s in result.slots)  # 0.312
    assert {s.price for s in result.slots} == {0.20, 0.30, 0.25}


def test_blocked_by_efficiency_is_not_no_qualifying_slot() -> None:
    """Slots qualify by level but none is worth it: its own, visible outcome."""
    blocked = plan(with_reference([0.38] * 32, 0.40), efficiency=0.78)
    assert blocked.reason is PlanReason.BLOCKED_BY_EFFICIENCY
    assert blocked.slots == ()
    assert blocked.energy_to_buy == 0.0
    assert blocked.energy_missing == pytest.approx(5.0)
    assert blocked.reference_price == pytest.approx(0.40)
    assert not blocked.reachable

    none = plan(with_reference([0.38] * 32, 0.40), efficiency=0.78, qualifying=set())
    assert none.reason is PlanReason.NO_QUALIFYING_SLOT


def test_ignoring_the_efficiency_charges_the_same_night() -> None:
    price_list = with_reference([0.38] * 32, 0.40)
    assert plan(price_list, efficiency=0.78).reason is PlanReason.BLOCKED_BY_EFFICIENCY
    ignored = plan(price_list, efficiency=0.78, ignore=True)
    assert ignored.reason is PlanReason.CHARGING_PLANNED
    assert len(ignored.slots) > 0
    assert ignored.reference_price == pytest.approx(0.40)


# -- energy ---------------------------------------------------------------------


def test_the_energy_stored_per_slot_carries_the_square_root_of_the_efficiency() -> None:
    result = plan(night([0.2] * 32), efficiency=0.64, level=90.0)  # 1 kWh missing
    root = math.sqrt(0.64)
    assert result.slots[0].stored_kwh == pytest.approx(0.5 * root)
    assert result.slots[0].drawn_kwh == pytest.approx(0.5)
    assert len(result.slots) == 3  # 0.4, 0.8, 1.2 kWh
    assert result.energy_to_buy == pytest.approx(1.0 / root)  # 1.25, not 1.5
    assert result.reachable


def test_the_energy_to_buy_is_the_drawn_energy_when_it_is_less() -> None:
    result = plan(night([0.2] * 2), efficiency=0.64)  # two slots, 1.0 kWh drawn
    assert result.energy_to_buy == pytest.approx(1.0)
    assert not result.reachable


def test_an_upper_bound_limits_the_energy_to_store() -> None:
    result = plan(night([0.2] * 32), max_energy=1.0)
    assert result.energy_missing == pytest.approx(1.0)
    assert len(result.slots) == 2


def test_an_upper_bound_above_what_is_missing_changes_nothing() -> None:
    result = plan(night([0.2] * 32), max_energy=50.0)
    assert result.energy_missing == pytest.approx(5.0)


def test_an_upper_bound_of_zero_means_nothing_to_do() -> None:
    result = plan(night([0.2] * 32), max_energy=0.0)
    assert result.reason is PlanReason.TARGET_REACHED
    assert result.slots == ()


def test_the_slot_length_comes_from_the_data() -> None:
    hourly = prices(NIGHT, [(0.2, "cheap")] * 8, step=timedelta(hours=1))
    assert slot_length(hourly) == timedelta(hours=1)
    result = plan(hourly, level=80.0, power=1000.0)  # 2 kWh, 1 kWh an hour
    assert len(result.slots) == 2
    assert result.slots[0].end - result.slots[0].start == timedelta(hours=1)
    assert result.slots[0].stored_kwh == pytest.approx(1.0)


def test_one_price_has_the_default_slot_length() -> None:
    assert slot_length(night([0.2])) == QUARTER
    assert slot_length([]) == QUARTER


# -- due now --------------------------------------------------------------------


def test_charging_is_due_inside_a_picked_slot_only() -> None:
    values = [0.30] * 32
    values[8] = 0.10
    result = plan(night(values), level=95.0)  # one slot of 0.5 kWh
    [slot] = result.slots
    assert slot.start == NIGHT + QUARTER * 8
    assert not result.is_due(slot.start - timedelta(seconds=1))
    assert result.is_due(slot.start)
    assert result.is_due(slot.end - timedelta(seconds=1))
    assert not result.is_due(slot.end)


def test_nothing_is_due_without_slots() -> None:
    assert not plan([]).is_due(NIGHT)


# -- days of 92 and 100 quarter-hours -------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")


def local_day(year: int, month: int, day: int) -> list[Price]:
    """Every quarter-hour of one local day, as the clock really runs."""
    start = datetime(year, month, day, tzinfo=BERLIN).astimezone(UTC)
    end = datetime(year, month, day + 1, tzinfo=BERLIN).astimezone(UTC)
    return prices(start, [(0.2, "cheap")] * int((end - start) / QUARTER))


@pytest.mark.parametrize(
    ("year", "month", "day", "count"),
    [(2026, 3, 29, 92), (2026, 10, 25, 100), (2026, 10, 5, 96)],
)
def test_a_day_of_92_or_100_slots_is_planned_through(
    year: int, month: int, day: int, count: int
) -> None:
    """The whole day is in the horizon, every slot once, none after midnight."""
    day_prices = local_day(year, month, day)
    assert len(day_prices) == count
    midnight = day_prices[0].start
    deadline = datetime(year, month, day + 1, tzinfo=BERLIN)
    result = plan(
        day_prices,
        now=midnight,
        deadline=deadline,
        level=0.0,
        capacity=1000.0,
        ignore=True,
    )
    assert len(result.slots) == count
    assert len({s.start for s in result.slots}) == count
    assert all(s.start < deadline for s in result.slots)
    assert result.energy_to_buy == pytest.approx(count * 0.5)


def test_the_slot_length_of_a_92_slot_day_is_a_quarter_hour() -> None:
    assert slot_length(local_day(2026, 3, 29)) == QUARTER
