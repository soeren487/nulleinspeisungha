"""The learned conversion from irradiance to PV production.

Pure functions, no Home Assistant. One factor per hour of day (UTC) is learned
from the House's own production history; recent records weigh more, and
quarter-hours in which the House was curtailing are left out because they show
what the House was allowed to produce, not what the panels could.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import NamedTuple

MIN_IRRADIANCE = 20.0
"""W/m² below which a record says too little about the panels to be used."""
HALF_LIFE_DAYS = 14.0
"""Days after which a record counts half as much."""
USABLE_DAYS = 14
"""Days with enough records that make the forecast usable."""
RECORDS_PER_DAY = 8
"""Records a day needs to count."""
SLOT_HOURS = 0.25


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    """What one finished quarter-hour showed."""

    start: datetime
    """Timezone-aware start of the quarter-hour."""
    production: float
    """Mean production of the House's PV Inverters in W."""
    irradiance: float | None
    """The irradiance forecast for the quarter-hour in W/m², if it was known."""
    curtailed: bool
    """Whether the House was curtailing at any moment in the quarter-hour."""


class ForecastSlot(NamedTuple):
    """The expected production of one quarter-hour."""

    start: datetime
    watts: float


@dataclass(frozen=True, slots=True)
class Factors:
    """Watts of production per W/m² of irradiance."""

    by_hour: Mapping[int, float]
    """Factor of each UTC hour that has participating records."""
    overall: float
    """Factor over all participating records; used for the other hours."""

    def for_hour(self, hour: int) -> float:
        """The factor for an hour of day (UTC)."""
        return self.by_hour.get(hour, self.overall)


def participates(record: HistoryRecord) -> bool:
    """Whether the record may take part in learning."""
    return (
        not record.curtailed
        and record.irradiance is not None
        and record.irradiance >= MIN_IRRADIANCE
    )


def learn(records: Iterable[HistoryRecord], now: datetime) -> Factors | None:
    """The factors the records give at ``now``; ``None`` without usable records."""
    production: dict[int, float] = {}
    irradiance: dict[int, float] = {}
    for record in records:
        if not participates(record):
            continue
        assert record.irradiance is not None
        age_days = max((now - record.start).total_seconds(), 0.0) / 86400
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        hour = record.start.astimezone(UTC).hour
        production[hour] = production.get(hour, 0.0) + weight * record.production
        irradiance[hour] = irradiance.get(hour, 0.0) + weight * record.irradiance
    total_irradiance = sum(irradiance.values())
    if total_irradiance <= 0:
        return None
    return Factors(
        by_hour={
            hour: max(production[hour] / irradiance[hour], 0.0) for hour in irradiance
        },
        overall=max(sum(production.values()) / total_irradiance, 0.0),
    )


def history_days(records: Iterable[HistoryRecord]) -> int:
    """Number of UTC dates with at least ``RECORDS_PER_DAY`` participating records."""
    per_day: dict[date, int] = {}
    for record in records:
        if participates(record):
            day = record.start.astimezone(UTC).date()
            per_day[day] = per_day.get(day, 0) + 1
    return sum(1 for count in per_day.values() if count >= RECORDS_PER_DAY)


def is_usable(records: Iterable[HistoryRecord]) -> bool:
    """Whether the history is long enough to trust the forecast."""
    return history_days(records) >= USABLE_DAYS


def production_for(factors: Factors, start: datetime, irradiance: float) -> float:
    """Expected production in W of the quarter-hour starting at ``start``."""
    if irradiance <= 0:
        return 0.0
    return max(factors.for_hour(start.astimezone(UTC).hour) * irradiance, 0.0)


def convert(
    factors: Factors, irradiance: Iterable[tuple[datetime, float]]
) -> tuple[ForecastSlot, ...]:
    """The PV Forecast for the given irradiance, sorted by start."""
    return tuple(
        ForecastSlot(start, production_for(factors, start, value))
        for start, value in sorted(irradiance, key=lambda point: point[0])
    )
