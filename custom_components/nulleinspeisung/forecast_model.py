"""The learned conversion from irradiance to PV production.

Pure functions, no Home Assistant. From the House's own production history two
conversions are learned per hour of day (UTC): one factor on the horizontal
irradiance and, where the history allows, a pair of factors on its direct and
diffuse parts. Recent records weigh more, and quarter-hours in which the House
was curtailing are left out because they show what the House was allowed to
produce, not what the panels could.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
MIN_SPLIT_RECORDS = 20
"""Records with both components an hour needs before its pair of factors is used."""
MIN_INDEPENDENCE = 0.05
"""Smallest share the determinant of the normal equations may have of the product
of their diagonal; below it direct and diffuse light are too nearly proportional
in the records to tell their factors apart."""


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
    """Whether the House held back its PV Inverters in the quarter-hour."""
    direct: float | None = None
    """The direct part of the horizontal irradiance in W/m², if it was known."""
    diffuse: float | None = None
    """The diffuse part of the horizontal irradiance in W/m², if it was known."""


class IrradiancePoint(NamedTuple):
    """The expected shortwave irradiance of one quarter-hour, with its two parts."""

    start: datetime
    """Timezone-aware (UTC) start of the quarter-hour."""
    irradiance: float
    """Mean horizontal irradiance in W/m²."""
    direct: float | None = None
    """The direct part of it in W/m², ``None`` when the answer has no value."""
    diffuse: float | None = None
    """The diffuse part of it in W/m², ``None`` when the answer has no value."""


class ForecastSlot(NamedTuple):
    """The expected production of one quarter-hour."""

    start: datetime
    watts: float


class ComponentFactors(NamedTuple):
    """Watts of production per W/m² of direct and of diffuse irradiance."""

    direct: float
    diffuse: float


@dataclass(frozen=True, slots=True)
class Factors:
    """Watts of production per W/m² of irradiance."""

    by_hour: Mapping[int, float]
    """Factor of each UTC hour that has participating records."""
    overall: float
    """Factor over all participating records; used for the other hours."""
    split_by_hour: Mapping[int, ComponentFactors] = field(default_factory=dict)
    """Factors on direct and diffuse irradiance of the UTC hours whose records
    tell the two apart; the other hours use ``for_hour``."""

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


class _Normal:
    """Weighted sums of the least-squares fit through the origin of two components."""

    def __init__(self) -> None:
        self.count = 0
        self.dd = self.df = self.ff = self.pd = self.pf = 0.0

    def add(
        self, weight: float, direct: float, diffuse: float, production: float
    ) -> None:
        self.count += 1
        self.dd += weight * direct * direct
        self.df += weight * direct * diffuse
        self.ff += weight * diffuse * diffuse
        self.pd += weight * production * direct
        self.pf += weight * production * diffuse

    def solve(self) -> ComponentFactors | None:
        """The pair of factors, ``None`` when the records do not support one."""
        if self.count < MIN_SPLIT_RECORDS:
            return None
        product = self.dd * self.ff
        det = product - self.df * self.df
        if product <= 0 or det < MIN_INDEPENDENCE * product:
            return None
        direct = (self.pd * self.ff - self.pf * self.df) / det
        diffuse = (self.pf * self.dd - self.pd * self.df) / det
        if direct < 0:
            direct, diffuse = 0.0, self.pf / self.ff
        elif diffuse < 0:
            direct, diffuse = self.pd / self.dd, 0.0
        return ComponentFactors(max(direct, 0.0), max(diffuse, 0.0))


def learn(records: Iterable[HistoryRecord], now: datetime) -> Factors | None:
    """The factors the records give at ``now``; ``None`` without usable records."""
    production: dict[int, float] = {}
    irradiance: dict[int, float] = {}
    normals: dict[int, _Normal] = {}
    for record in records:
        if not participates(record):
            continue
        assert record.irradiance is not None
        age_days = max((now - record.start).total_seconds(), 0.0) / 86400
        weight = 0.5 ** (age_days / HALF_LIFE_DAYS)
        hour = record.start.astimezone(UTC).hour
        production[hour] = production.get(hour, 0.0) + weight * record.production
        irradiance[hour] = irradiance.get(hour, 0.0) + weight * record.irradiance
        if record.direct is not None and record.diffuse is not None:
            normals.setdefault(hour, _Normal()).add(
                weight, record.direct, record.diffuse, record.production
            )
    total_irradiance = sum(irradiance.values())
    if total_irradiance <= 0:
        return None
    return Factors(
        by_hour={
            hour: max(production[hour] / irradiance[hour], 0.0) for hour in irradiance
        },
        overall=max(sum(production.values()) / total_irradiance, 0.0),
        split_by_hour={
            hour: pair
            for hour, normal in normals.items()
            if (pair := normal.solve()) is not None
        },
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


def production_for(
    factors: Factors,
    start: datetime,
    irradiance: float,
    direct: float | None = None,
    diffuse: float | None = None,
) -> float:
    """Expected production in W of the quarter-hour starting at ``start``.

    Uses the pair of factors of the hour when it has one and both components are
    known, else the single factor on the horizontal irradiance.
    """
    if irradiance <= 0:
        return 0.0
    hour = start.astimezone(UTC).hour
    pair = factors.split_by_hour.get(hour)
    if pair is not None and direct is not None and diffuse is not None:
        return max(pair.direct * direct + pair.diffuse * diffuse, 0.0)
    return max(factors.for_hour(hour) * irradiance, 0.0)


def convert(
    factors: Factors, points: Iterable[IrradiancePoint]
) -> tuple[ForecastSlot, ...]:
    """The PV Forecast for the given irradiance, sorted by start."""
    return tuple(
        ForecastSlot(
            p.start,
            production_for(factors, p.start, p.irradiance, p.direct, p.diffuse),
        )
        for p in sorted(points, key=lambda point: point.start)
    )
