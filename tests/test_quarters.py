"""The quarter-hour helpers, calculated directly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from custom_components.nulleinspeisung.quarters import (
    quarter_start,
    slot_length,
    slots,
)

DEADLINE = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


@dataclass(frozen=True)
class Price:
    start: datetime


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


def test_the_slot_length_comes_from_the_data() -> None:
    hourly = [Price(DEADLINE + timedelta(hours=i)) for i in range(4)]
    assert slot_length(hourly) == timedelta(hours=1)
    assert slot_length(list(reversed(hourly))) == timedelta(hours=1)


def test_fewer_than_two_prices_give_a_quarter_hour() -> None:
    assert slot_length([Price(DEADLINE)]) == SLOT
    assert slot_length([]) == SLOT


def test_a_local_time_gives_the_quarter_hour_in_utc() -> None:
    berlin = timezone(timedelta(hours=2))
    moment = datetime(2026, 10, 5, 7, 22, 59, tzinfo=berlin)
    assert quarter_start(moment) == datetime(2026, 10, 5, 5, 15, tzinfo=UTC)
