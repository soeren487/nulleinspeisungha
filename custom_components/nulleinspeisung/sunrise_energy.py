"""What the battery must hold at sunrise, given the PV Forecast and the Expected Load.

Pure functions, no Home Assistant; the time is passed in. All series are
mappings from the start of a quarter-hour (timezone-aware) to W. Energies are in
kWh. The efficiency is the Battery Efficiency as a round-trip fraction; the
discharging half of the loss is its square root.

Known simplification: the production of Battery-backed Inverters by day is not
counted, so the energy needed errs on the side of a little more. Their support
at night (DC Battery Support) lowers what the battery delivers in
``battery_at_sunrise``.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

SLOT = timedelta(minutes=15)
SURPLUS_WINDOW = timedelta(hours=24)
"""How far after the deadline the forecast surplus looks."""
BRIDGE_LIMIT = timedelta(hours=24)
"""How long the House must be bridged when no qualifying quarter-hour is known."""


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


def period_end(
    deadline: datetime, prices: Sequence[Any], qualifying: Collection[Any]
) -> datetime:
    """The end of the period to bridge after ``deadline``.

    The start of the first price slot at or after the deadline whose level
    qualifies for charging, else the deadline plus 24 hours. ``prices`` have
    ``start`` and ``level``.
    """
    starts = [
        p.start
        for p in prices
        if p.start >= deadline and p.level in qualifying  # aware datetimes compare
    ]
    return min(starts, default=deadline + BRIDGE_LIMIT)


def energy_needed_at_sunrise(
    deadline: datetime,
    load: Mapping[datetime, float],
    forecast: Mapping[datetime, float],
    share: float,
    end: datetime,
    efficiency: float,
) -> float | None:
    """The energy the battery must hold at ``deadline``, in kWh.

    From the deadline to ``end`` the running sum of (Expected Load minus
    ``share`` times PV Forecast) times the slot length is taken; its peak, not
    below 0, is what the House must take out of the battery. Dividing by the
    square root of the efficiency gives what the battery must hold. A slot without
    a forecast value counts as no production; a slot without an Expected Load makes
    the result ``None``.
    """
    running = peak = 0.0
    for slot, hours in slots(deadline, end):
        watts = load.get(slot)
        if watts is None:
            return None
        running += (watts - share * forecast.get(slot, 0.0)) * hours / 1000.0
        peak = max(peak, running)
    return peak / math.sqrt(efficiency)


def battery_at_sunrise(
    now: datetime,
    deadline: datetime,
    capacity_kwh: float,
    charge_level: float,
    load: Mapping[datetime, float],
    blocks: Collection[datetime],
    efficiency: float,
    support: Mapping[datetime, float] | None = None,
) -> float | None:
    """The content expected at ``deadline`` without any charging, in kWh.

    From ``capacity_kwh`` times ``charge_level`` (percent) the House's load,
    divided by the square root of the efficiency, is taken out in every
    quarter-hour from ``now`` that is not in ``blocks`` (the starts of
    Discharge Blocks); never below 0. The current quarter-hour counts with its
    remaining fraction. ``None`` when a needed Expected Load is missing.

    ``support`` is the DC Battery Support in W per quarter-hour: the AC Battery
    then delivers the load minus the support, not below 0, in a quarter-hour
    that is not a Discharge Block.
    """
    content = capacity_kwh * charge_level / 100.0
    root = math.sqrt(efficiency)
    for slot, hours in slots(now, deadline):
        if slot in blocks:
            continue
        watts = load.get(slot)
        if watts is None:
            return None
        if support is not None:
            watts = max(watts - support.get(slot, 0.0), 0.0)
        content = max(content - watts * hours / 1000.0 / root, 0.0)
    return content


def energy_to_store(needed: float, content: float) -> float:
    """What must still be stored before sunrise: needed minus content, not below 0."""
    return max(needed - content, 0.0)


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
