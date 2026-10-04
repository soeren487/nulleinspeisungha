"""The Expected Load model, tested directly."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.nulleinspeisung.expected_load_model import (
    LoadRecord,
    fallback_watts,
    learn,
    learned_days,
    slot_of,
)

BERLIN = ZoneInfo("Europe/Berlin")
FALLBACK = 500.0
NOW = datetime(2026, 10, 14, 20, 0, tzinfo=BERLIN)


def day(
    d: date,
    watts: Callable[[int], float] = lambda slot: 100.0,
    first: int = 0,
    last: int | None = None,
    tz: ZoneInfo = BERLIN,
) -> list[LoadRecord]:
    """The records of a local day: every quarter-hour of it, or slots from..to."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(UTC)
    end = datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(days=1)
    records = []
    while start < end.astimezone(UTC):
        slot = slot_of(start, tz)
        if first <= slot and (last is None or slot <= last):
            records.append(LoadRecord(start, watts(slot)))
        start += timedelta(minutes=15)
    return records


def days_before_now(n: int, **kwargs) -> list[LoadRecord]:
    records: list[LoadRecord] = []
    for i in range(1, n + 1):
        records += day(NOW.date() - timedelta(days=i), **kwargs)
    return records


def at(slot: int, d: date | None = None) -> datetime:
    d = d or NOW.date()
    return datetime(d.year, d.month, d.day, slot // 4, slot % 4 * 15, tzinfo=BERLIN)


def test_the_fallback_is_spread_evenly_over_the_day() -> None:
    """12 kWh a day is 500 W at every hour."""
    assert fallback_watts(12) == 500.0
    assert fallback_watts(10) == pytest.approx(416.667, abs=0.001)


def test_a_slot_is_the_mean_of_that_quarter_hour_on_past_days() -> None:
    """Seven days with 100 W by night and 300 W by day: that is what is expected."""
    records = days_before_now(7, watts=lambda slot: 300.0 if 32 <= slot < 72 else 100.0)
    profile = learn(records, BERLIN, FALLBACK, NOW)
    assert profile.learned
    assert profile.learned_days == 7
    assert profile.watts_at(at(4)) == pytest.approx(100.0)
    assert profile.watts_at(at(40)) == pytest.approx(300.0)
    # The same slot on any future day gives the same answer.
    assert profile.watts_at(at(40, NOW.date() + timedelta(days=3))) == pytest.approx(
        300.0
    )


def test_recent_days_weigh_more() -> None:
    """20:00 was 400 W yesterday and 100 W eight days ago: 300 W, not 250 W."""
    records = days_before_now(7, last=71)
    records += [
        LoadRecord(at(80, NOW.date() - timedelta(days=1)), 400.0),
        LoadRecord(at(80, NOW.date() - timedelta(days=8)), 100.0),
    ]
    profile = learn(records, BERLIN, FALLBACK, NOW)
    # Seven days older is exactly half as heavy: (2 * 400 + 1 * 100) / 3.
    assert profile.watts_at(at(80)) == pytest.approx(300.0)


def test_a_slot_without_records_uses_the_fallback() -> None:
    """Seven learned days that never covered the evening: 500 W there."""
    profile = learn(days_before_now(7, last=71), BERLIN, FALLBACK, NOW)
    assert profile.learned
    assert profile.watts_at(at(10)) == pytest.approx(100.0)
    assert profile.watts_at(at(90)) == FALLBACK


def test_less_than_a_week_uses_the_fallback_everywhere() -> None:
    """Six learned days are not a week: even slots with records show the fallback."""
    profile = learn(days_before_now(6), BERLIN, FALLBACK, NOW)
    assert not profile.learned
    assert profile.learned_days == 6
    assert profile.watts_at(at(10)) == FALLBACK
    assert profile.watts_at(at(90)) == FALLBACK


def test_no_records_at_all_uses_the_fallback() -> None:
    profile = learn([], BERLIN, FALLBACK, NOW)
    assert not profile.learned
    assert profile.learned_days == 0
    assert profile.watts_at(at(0)) == FALLBACK


def test_a_day_needs_72_records_to_count() -> None:
    """71 records of a day are not a learned day, 72 are."""
    short = days_before_now(6) + day(NOW.date() - timedelta(days=7), last=70)
    assert learned_days(short, BERLIN) == 6
    assert not learn(short, BERLIN, FALLBACK, NOW).learned
    enough = days_before_now(6) + day(NOW.date() - timedelta(days=7), last=71)
    assert learned_days(enough, BERLIN) == 7
    assert learn(enough, BERLIN, FALLBACK, NOW).learned


def test_slots_follow_local_time_across_the_end_of_summer_time() -> None:
    """Berlin has 25 hours on 2026-10-25: 100 records, one day, 02:00 happens twice."""
    d = date(2026, 10, 25)
    long_day = day(d)
    assert len(long_day) == 100
    # 00:00Z and 01:00Z are both 02:00 local (CEST, then CET).
    assert slot_of(datetime(2026, 10, 25, 0, 0, tzinfo=UTC), BERLIN) == 8
    assert slot_of(datetime(2026, 10, 25, 1, 0, tzinfo=UTC), BERLIN) == 8
    assert slot_of(datetime(2026, 10, 25, 2, 0, tzinfo=UTC), BERLIN) == 12
    assert learned_days(long_day, BERLIN) == 1
    # The first 02:00 hour consumed 200 W, the second 400 W, everything else 100 W.
    long_day = [
        LoadRecord(
            r.start,
            (200.0 if r.start < datetime(2026, 10, 25, 1, tzinfo=UTC) else 400.0)
            if slot_of(r.start, BERLIN) == 8
            else 100.0,
        )
        for r in long_day
    ]
    records = long_day
    for i in range(1, 7):
        records += day(d + timedelta(days=i), first=12, last=83)
    # Evaluated exactly a week after the long day, all its records weigh the same
    # within a hour: the 02:00 slot is the mean of the 200 W and the 400 W hours.
    now = datetime(2026, 11, 1, 3, tzinfo=BERLIN)
    profile = learn(records, BERLIN, FALLBACK, now)
    assert profile.learned
    assert profile.watts_at(at(8)) == pytest.approx(300.0, abs=3.0)
    assert profile.watts_at(at(9)) == pytest.approx(100.0)


def test_slots_follow_local_time_across_the_start_of_summer_time() -> None:
    """Berlin has 23 hours on 2026-03-29: 92 records still make a day."""
    d = date(2026, 3, 29)
    short_day = day(d)
    assert len(short_day) == 92
    assert learned_days(short_day, BERLIN) == 1
    slots = {slot_of(r.start, BERLIN) for r in short_day}
    assert not slots & {8, 9, 10, 11}
    assert slot_of(datetime(2026, 3, 29, 1, 0, tzinfo=UTC), BERLIN) == 12  # 03:00 CEST
    # Seven such days: the missing slot falls back, its neighbours are learned.
    now = datetime(2026, 4, 6, 12, tzinfo=BERLIN)
    records = short_day
    for i in range(1, 7):
        records += day(d + timedelta(days=i))
    profile = learn(records, BERLIN, FALLBACK, now)
    assert profile.learned
    assert profile.watts_at(datetime(2026, 4, 7, 1, 0, tzinfo=BERLIN)) == pytest.approx(
        100.0
    )


def test_negative_values_never_appear() -> None:
    """Even a stray negative record gives 0 W, and a negative fallback gives 0 W."""
    records = days_before_now(7, watts=lambda slot: -50.0)
    profile = learn(records, BERLIN, FALLBACK, NOW)
    assert profile.learned
    assert all(w >= 0 for w in profile.by_slot.values())
    assert profile.watts_at(at(10)) == 0.0
    assert fallback_watts(-5) == 0.0
