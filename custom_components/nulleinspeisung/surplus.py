"""The forecast surplus: what the PV Forecast brings above the Expected Load.

A pure function, no Home Assistant. All series are mappings from the start of a
quarter-hour (timezone-aware) to W. Energies are in kWh.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from .quarters import slots

SURPLUS_WINDOW = timedelta(hours=24)
"""How far after the deadline the forecast surplus looks."""


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
