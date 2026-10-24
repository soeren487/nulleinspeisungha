"""Quarter-hour helpers for the periods around sunrise and the forecast surplus.

Pure functions, no Home Assistant; the time is passed in. All series are
mappings from the start of a quarter-hour (timezone-aware) to W. Energies are in
kWh.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta

SLOT = timedelta(minutes=15)
SURPLUS_WINDOW = timedelta(hours=24)
"""How far after the deadline the forecast surplus looks."""


def quarter_start(moment: datetime) -> datetime:
    """The start (UTC) of the quarter-hour that ``moment`` lies in."""
    seconds = int(moment.timestamp()) // int(SLOT.total_seconds()) * 900
    return datetime.fromtimestamp(seconds, UTC)


def slots(start: datetime, end: datetime) -> Iterator[tuple[datetime, float]]:
    """(quarter-hour start, hours of it inside [start, end)) for each quarter."""
    slot = quarter_start(start)
    while slot < end:
        inside = min(slot + SLOT, end) - max(slot, start)
        if inside > timedelta(0):
            yield slot, inside.total_seconds() / 3600.0
        slot += SLOT


def forecast_surplus(
    deadline: datetime,
    load: Mapping[datetime, float],
    forecast: Mapping[datetime, float],
    share: float,
) -> float | None:
    """Over 24 hours from ``deadline``, the sum of the positive parts of the
    counted forecast minus the Expected Load, in kWh; ``None`` without a load.
    """
    total = 0.0
    for slot, hours in slots(deadline, deadline + SURPLUS_WINDOW):
        watts = load.get(slot)
        if watts is None:
            return None
        total += max(share * forecast.get(slot, 0.0) - watts, 0.0) * hours / 1000.0
    return total
