"""Where the sun is, for the location Home Assistant is configured with."""

from __future__ import annotations

import astral.sun
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_observer
from homeassistant.util import dt as dt_util


def sun_elevation(hass: HomeAssistant) -> float:
    """Elevation of the sun in degrees above the horizon, right now."""
    return float(astral.sun.elevation(get_astral_observer(hass), dt_util.utcnow()))
