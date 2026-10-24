"""The quarter-hour helpers and the forecast surplus, calculated directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nulleinspeisung.sunrise_energy import (
    forecast_surplus,
    quarter_start,
    slots,
)

DEADLINE = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def series(start: datetime, hours: float, watts) -> dict[datetime, float]:
    """A value per quarter-hour from ``start``; ``watts`` is a number or f(index)."""
    count = round(hours * 4)
    return {
        start + i * SLOT: (watts(i) if callable(watts) else watts) for i in range(count)
    }


# -- the quarter-hours -------------------------------------------------------


def test_a_quarter_hour_starts_at_a_multiple_of_fifteen_minutes() -> None:
    moment = datetime(2026, 10, 5, 5, 7, 31, tzinfo=UTC)
    assert quarter_start(moment) == datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
    assert quarter_start(DEADLINE + SLOT) == DEADLINE + SLOT


def test_the_slots_of_a_period_count_the_part_inside_it() -> None:
    start = DEADLINE + timedelta(minutes=5)
    end = DEADLINE + timedelta(minutes=35)
    assert list(slots(start, end)) == [
        (DEADLINE, pytest.approx(10 / 60)),
        (DEADLINE + SLOT, pytest.approx(0.25)),
        (DEADLINE + 2 * SLOT, pytest.approx(5 / 60)),
    ]
    assert list(slots(end, start)) == []


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
