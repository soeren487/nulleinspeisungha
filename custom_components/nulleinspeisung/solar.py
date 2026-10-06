"""Where the sun is, for Home Assistant's location or for a place given."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import astral
import astral.sun
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_observer
from homeassistant.util import dt as dt_util


def sun_elevation(hass: HomeAssistant) -> float:
    """Elevation of the sun in degrees above the horizon, right now."""
    return float(astral.sun.elevation(get_astral_observer(hass), dt_util.utcnow()))


def next_sunrise(latitude: float, longitude: float, now: datetime) -> datetime:
    """The next sunrise after ``now`` at a place, in UTC.

    Where the sun does not rise within the next 24 hours, it is ``now`` plus
    24 hours.
    """
    observer = astral.Observer(latitude, longitude)
    instant = now.astimezone(UTC)
    day = instant.date()
    candidates = []
    for offset in (-1, 0, 1, 2):
        try:
            rise = astral.sun.sunrise(observer, day + timedelta(days=offset), UTC)
        except ValueError:  # the sun does not rise or set that day
            continue
        if rise > instant:
            candidates.append(rise)
    limit = instant + timedelta(hours=24)
    found = min(candidates, default=None)
    return found if found is not None and found <= limit else limit
