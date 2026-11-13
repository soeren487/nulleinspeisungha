"""The quarter-hour: the unit the integration plans, records and forecasts in.

Pure functions, no Home Assistant. A quarter-hour is named by its start, a
timezone-aware datetime in UTC.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Protocol

SLOT = timedelta(minutes=15)
"""Length of one quarter-hour."""
SLOT_SECONDS = int(SLOT.total_seconds())


class HasStart(Protocol):
    """What ``slot_length`` needs to know of a price slot."""

    @property
    def start(self) -> datetime:
        """Timezone-aware start of the slot."""


def quarter_start(moment: datetime) -> datetime:
    """The start (UTC) of the quarter-hour that ``moment`` lies in."""
    seconds = int(moment.timestamp()) // SLOT_SECONDS * SLOT_SECONDS
    return datetime.fromtimestamp(seconds, UTC)


def slots(start: datetime, end: datetime) -> Iterator[tuple[datetime, float]]:
    """(quarter-hour start, hours of it inside [start, end)) for each quarter."""
    slot = quarter_start(start)
    while slot < end:
        inside = min(slot + SLOT, end) - max(slot, start)
        if inside > timedelta(0):
            yield slot, inside.total_seconds() / 3600.0
        slot += SLOT


def slot_length(prices: Sequence[HasStart]) -> timedelta:
    """The smallest positive step between two starts, else a quarter-hour.

    Compared in UTC: datetimes of one zone with a daylight-saving fold compare and
    subtract by their wall clock, in UTC every comparison is of real time.
    """
    steps = [
        step
        for a, b in pairwise(sorted(p.start.astimezone(UTC) for p in prices))
        if (step := b - a) > timedelta(0)
    ]
    return min(steps) if steps else SLOT
