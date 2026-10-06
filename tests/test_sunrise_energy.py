"""The energy to hold at sunrise, calculated directly."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nulleinspeisung.sunrise_energy import (
    battery_at_sunrise,
    energy_needed_at_sunrise,
    energy_to_store,
    forecast_surplus,
    period_end,
)

DEADLINE = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 4, 20, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)
ROOT = math.sqrt(0.64)  # 0.8


def series(start: datetime, hours: float, watts) -> dict[datetime, float]:
    """A value per quarter-hour from ``start``; ``watts`` is a number or f(index)."""
    count = round(hours * 4)
    return {
        start + i * SLOT: (watts(i) if callable(watts) else watts) for i in range(count)
    }


@dataclass
class Price:
    start: datetime
    level: str


# -- energy needed at sunrise ----------------------------------------------


def test_a_sunny_day_needs_nothing() -> None:
    """The counted production is above the load in every quarter-hour."""
    end = DEADLINE + timedelta(hours=6)
    load = series(DEADLINE, 6, 300.0)
    forecast = series(DEADLINE, 6, 1000.0)
    assert energy_needed_at_sunrise(DEADLINE, load, forecast, 0.7, end, 1.0) == 0.0


def test_a_dull_day_needs_the_whole_bridge() -> None:
    """No production: 400 W for 6 hours is 2.4 kWh."""
    end = DEADLINE + timedelta(hours=6)
    load = series(DEADLINE, 6, 400.0)
    assert energy_needed_at_sunrise(DEADLINE, load, {}, 0.7, end, 1.0) == (
        pytest.approx(2.4)
    )


def test_the_peak_is_taken_not_the_end_value() -> None:
    """A deficit of 1 kWh in the morning, then a bigger surplus: 1 kWh is needed."""
    end = DEADLINE + timedelta(hours=6)
    load = series(DEADLINE, 6, 500.0)
    # hours 0-2: no production (deficit 500 W * 2 h = 1 kWh); hours 2-6: 1500 W
    forecast = series(DEADLINE, 6, lambda i: 0.0 if i < 8 else 1500.0)
    needed = energy_needed_at_sunrise(DEADLINE, load, forecast, 1.0, end, 1.0)
    assert needed == pytest.approx(1.0)
    # the end value of the running sum is -3 kWh, below 0
    total = sum(load[s] - forecast[s] for s in load) * 0.25 / 1000
    assert total == pytest.approx(-3.0)


def test_only_the_counted_share_of_the_forecast_is_used() -> None:
    end = DEADLINE + timedelta(hours=4)
    load = series(DEADLINE, 4, 500.0)
    forecast = series(DEADLINE, 4, 400.0)
    full = energy_needed_at_sunrise(DEADLINE, load, forecast, 1.0, end, 1.0)
    most = energy_needed_at_sunrise(DEADLINE, load, forecast, 0.7, end, 1.0)
    half = energy_needed_at_sunrise(DEADLINE, load, forecast, 0.5, end, 1.0)
    assert full == pytest.approx(0.4)  # 100 W * 4 h
    assert most == pytest.approx(0.88)  # 220 W * 4 h
    assert half == pytest.approx(1.2)  # 300 W * 4 h


def test_the_energy_is_divided_by_the_square_root_of_the_efficiency() -> None:
    end = DEADLINE + timedelta(hours=2)
    load = series(DEADLINE, 2, 500.0)
    assert energy_needed_at_sunrise(DEADLINE, load, {}, 0.7, end, 0.64) == (
        pytest.approx(1.0 / ROOT)
    )


def test_missing_forecast_values_count_as_no_production() -> None:
    end = DEADLINE + timedelta(hours=2)
    load = series(DEADLINE, 2, 500.0)
    forecast = series(DEADLINE, 1, 500.0)  # the second hour is not known
    assert energy_needed_at_sunrise(DEADLINE, load, forecast, 1.0, end, 1.0) == (
        pytest.approx(0.5)
    )


def test_a_missing_load_makes_the_result_unknown() -> None:
    end = DEADLINE + timedelta(hours=2)
    load = series(DEADLINE, 1, 500.0)
    assert energy_needed_at_sunrise(DEADLINE, load, {}, 0.7, end, 1.0) is None


def test_a_deadline_inside_a_quarter_hour_counts_the_rest_of_it() -> None:
    deadline = DEADLINE + timedelta(minutes=5)
    end = DEADLINE + timedelta(hours=1)
    load = series(DEADLINE, 1, 600.0)
    # 55 minutes at 600 W
    assert energy_needed_at_sunrise(deadline, load, {}, 0.7, end, 1.0) == (
        pytest.approx(0.55)
    )


# -- the end of the period ------------------------------------------------------


def _prices(levels: dict[int, str]) -> list[Price]:
    """Prices of 48 quarter-hours from ``NOW`` (index -> level, else normal)."""
    return [Price(NOW + i * SLOT, levels.get(i, "normal")) for i in range(96)]


def test_the_period_ends_at_the_first_qualifying_quarter_hour_after_the_deadline() -> (
    None
):
    # index 36 is 05:00 (the deadline), 40 is 06:00, 44 is 07:00. 8 is before.
    prices = _prices({8: "cheap", 41: "cheap", 44: "very_cheap"})
    assert DEADLINE == NOW + 36 * SLOT
    assert period_end(DEADLINE, prices, {"cheap", "very_cheap"}) == NOW + 41 * SLOT


def test_a_qualifying_quarter_hour_at_the_deadline_ends_the_period_at_once() -> None:
    prices = _prices({36: "cheap"})
    assert period_end(DEADLINE, prices, {"cheap"}) == DEADLINE


def test_without_a_qualifying_quarter_hour_the_period_is_24_hours() -> None:
    prices = _prices({8: "cheap"})
    assert period_end(DEADLINE, prices, {"cheap"}) == DEADLINE + timedelta(hours=24)
    assert period_end(DEADLINE, [], {"cheap"}) == DEADLINE + timedelta(hours=24)


def test_the_needed_energy_stops_at_the_end_of_the_period() -> None:
    """Only the quarter-hours until the qualifying one count."""
    end = DEADLINE + timedelta(hours=1)
    load = series(DEADLINE, 8, 800.0)
    assert energy_needed_at_sunrise(DEADLINE, load, {}, 0.7, end, 1.0) == (
        pytest.approx(0.8)
    )


# -- the content at sunrise -----------------------------------------------------


def test_the_content_at_sunrise_without_blocks() -> None:
    """10 kWh at 50 %, 9 hours at 400 W, efficiency 0.64: 5 - 3.6 / 0.8 = 0.5."""
    load = series(NOW, 12, 400.0)
    content = battery_at_sunrise(NOW, DEADLINE, 10.0, 50.0, load, set(), 0.64)
    assert content == pytest.approx(5.0 - 3.6 / ROOT)


def test_quarter_hours_in_a_block_do_not_discharge() -> None:
    load = series(NOW, 12, 400.0)
    blocks = {NOW + i * SLOT for i in range(8, 16)}  # two hours
    content = battery_at_sunrise(NOW, DEADLINE, 10.0, 50.0, load, blocks, 0.64)
    assert content == pytest.approx(5.0 - 2.8 / ROOT)


def test_the_current_quarter_hour_counts_with_its_remaining_fraction() -> None:
    now = NOW + timedelta(minutes=5)  # 10 minutes of the quarter-hour remain
    deadline = NOW + timedelta(minutes=15)
    load = series(NOW, 1, 600.0)
    content = battery_at_sunrise(now, deadline, 10.0, 100.0, load, set(), 1.0)
    assert content == pytest.approx(10.0 - 0.6 * 10 / 60)


def test_the_content_never_falls_below_zero() -> None:
    load = series(NOW, 12, 5000.0)
    assert battery_at_sunrise(NOW, DEADLINE, 10.0, 10.0, load, set(), 1.0) == 0.0


def test_the_content_is_unknown_without_a_load_but_not_in_a_block() -> None:
    load = series(NOW, 1, 400.0)
    assert battery_at_sunrise(NOW, DEADLINE, 10.0, 50.0, load, set(), 1.0) is None
    blocks = {NOW + i * SLOT for i in range(4, 36)}
    assert battery_at_sunrise(NOW, DEADLINE, 10.0, 50.0, load, blocks, 1.0) == (
        pytest.approx(5.0 - 0.4)
    )


def test_after_the_deadline_the_content_is_what_the_battery_holds() -> None:
    assert battery_at_sunrise(DEADLINE, DEADLINE, 10.0, 40.0, {}, set(), 0.78) == 4.0


# -- the combination -------------------------------------------------------------


@pytest.mark.parametrize(
    ("needed", "content", "expected"),
    [(3.0, 1.0, 2.0), (1.0, 3.0, 0.0), (0.0, 0.0, 0.0)],
)
def test_the_energy_to_store_is_never_negative(
    needed: float, content: float, expected: float
) -> None:
    assert energy_to_store(needed, content) == expected


# -- the surplus ------------------------------------------------------------------


def test_the_surplus_is_the_sum_of_the_positive_parts() -> None:
    """Two hours 300 W above the load, two hours 200 W below it: 0.6 kWh."""
    load = series(DEADLINE, 24, 500.0)
    forecast = series(DEADLINE, 4, lambda i: 800.0 if i < 8 else 300.0)
    # counted share 100 %
    assert forecast_surplus(DEADLINE, load, forecast, 1.0) == pytest.approx(0.6)
    # at 70 %: 560 W and 210 W: 60 W * 2 h
    assert forecast_surplus(DEADLINE, load, forecast, 0.7) == pytest.approx(0.12)


def test_the_surplus_looks_24_hours_ahead_and_needs_the_load() -> None:
    load = series(DEADLINE, 24, 0.0)
    forecast = series(DEADLINE, 30, 1000.0)
    assert forecast_surplus(DEADLINE, load, forecast, 1.0) == pytest.approx(24.0)
    assert forecast_surplus(DEADLINE, series(DEADLINE, 1, 0.0), forecast, 1.0) is None
