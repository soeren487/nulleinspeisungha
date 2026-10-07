"""The Expected Load of a House: a weighted mean of past days, per quarter-hour.

Pure functions, no Home Assistant. A record belongs to a slot of the day, its
local time in quarter-hours (0 to 95): consumption follows the clock people
live by, so days with a daylight-saving change simply have slots with one
record fewer or more. Recent records weigh more. As long as fewer than
``LEARNED_DAYS`` days are complete enough, and for slots without any record, a
configured daily consumption spread evenly over the day is used instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo

from .quarter_recorder import QuarterRecord

HALF_LIFE_DAYS = 7.0
"""Days after which a record counts half as much."""
LEARNED_DAYS = 7
"""Days with enough records that make the model learned."""
RECORDS_PER_DAY = 72
"""Records a day needs to count: three quarters of the 96 quarter-hours."""
SLOTS_PER_DAY = 96
SLOT_HOURS = 0.25


LoadRecord = QuarterRecord
"""What one finished quarter-hour of consumption showed."""


def fallback_watts(daily_kwh: float) -> float:
    """A daily consumption in kWh spread evenly over the day, in W."""
    return max(daily_kwh, 0.0) * 1000 / 24


def slot_of(start: datetime, tz: tzinfo) -> int:
    """The slot of the day (0 to 95) of a quarter-hour start, in local time."""
    local = start.astimezone(tz)
    return local.hour * 4 + local.minute // 15


@dataclass(frozen=True, slots=True)
class LoadProfile:
    """The Expected Load for every slot of the day, as learned at some moment."""

    tz: tzinfo
    fallback: float
    """The Expected Load in W wherever nothing has been learned."""
    by_slot: Mapping[int, float] = field(default_factory=dict)
    """Weighted mean of each slot that has records; empty while not learned."""
    learned_days: int = 0
    """Days with at least ``RECORDS_PER_DAY`` records."""

    @property
    def learned(self) -> bool:
        """Whether enough days are complete for the learned values to be used."""
        return self.learned_days >= LEARNED_DAYS

    def watts_at(self, start: datetime) -> float:
        """The Expected Load in W of the quarter-hour starting at ``start``."""
        return self.by_slot.get(slot_of(start, self.tz), self.fallback)


def learned_days(records: Iterable[LoadRecord], tz: tzinfo) -> int:
    """Number of local days with at least ``RECORDS_PER_DAY`` records."""
    per_day: dict[date, int] = {}
    for record in records:
        day = record.start.astimezone(tz).date()
        per_day[day] = per_day.get(day, 0) + 1
    return sum(1 for count in per_day.values() if count >= RECORDS_PER_DAY)


def learn(
    records: Iterable[LoadRecord], tz: tzinfo, fallback: float, now: datetime
) -> LoadProfile:
    """The profile the records give at ``now``; ``fallback`` is in W."""
    records = list(records)
    days = learned_days(records, tz)
    if days < LEARNED_DAYS:
        return LoadProfile(tz, fallback, {}, days)
    weighted: dict[int, float] = {}
    weights: dict[int, float] = {}
    for record in records:
        age_days = max((now - record.start).total_seconds(), 0.0) / 86400
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        slot = slot_of(record.start, tz)
        weighted[slot] = weighted.get(slot, 0.0) + weight * max(record.watts, 0.0)
        weights[slot] = weights.get(slot, 0.0) + weight
    return LoadProfile(
        tz,
        fallback,
        {slot: weighted[slot] / weights[slot] for slot in weights},
        days,
    )
