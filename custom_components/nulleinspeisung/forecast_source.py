"""The irradiance forecast from Open-Meteo: one request, parsed and checked."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import DOMAIN, OPEN_METEO_REQUEST_TIMEOUT, OPEN_METEO_URL


class OpenMeteoError(Exception):
    """Open-Meteo could not be reached or gave an answer that is not usable."""


class IrradiancePoint(NamedTuple):
    """The expected shortwave irradiance of one quarter-hour."""

    start: datetime
    """Timezone-aware (UTC) start of the quarter-hour."""
    irradiance: float
    """Mean irradiance in W/m²."""


class OpenMeteoClient:
    """Asks Open-Meteo for irradiance, using a shared HTTP session."""

    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        """Create a client; no API key is needed."""
        self._session = session
        self._headers = {"User-Agent": user_agent}

    async def async_irradiance(
        self, latitude: float, longitude: float
    ) -> tuple[IrradiancePoint, ...]:
        """Today's and tomorrow's irradiance per quarter-hour, sorted by start.

        Quarter-hours without a value are left out.
        """
        params = {
            "latitude": str(latitude),
            "longitude": str(longitude),
            "minutely_15": "shortwave_radiation",
            "forecast_days": "2",
            "timeformat": "unixtime",
            "timezone": "UTC",
        }
        try:
            async with self._session.get(
                OPEN_METEO_URL,
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=OPEN_METEO_REQUEST_TIMEOUT),
            ) as response:
                response.raise_for_status()
                answer = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise OpenMeteoError(str(err) or type(err).__name__) from err
        return parse_irradiance(answer)


def parse_irradiance(answer: Any) -> tuple[IrradiancePoint, ...]:
    """The irradiance points of an Open-Meteo answer."""
    if not isinstance(answer, dict):
        raise OpenMeteoError("Unexpected answer")
    if answer.get("error"):
        raise OpenMeteoError(f"Open-Meteo reported: {answer.get('reason')}")
    try:
        block = answer["minutely_15"]
        times = block["time"]
        values = block["shortwave_radiation"]
        if len(times) != len(values):
            raise ValueError("lengths differ")
        points = [
            IrradiancePoint(datetime.fromtimestamp(int(t), UTC), float(v))
            for t, v in zip(times, values, strict=True)
            if v is not None
        ]
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as err:
        raise OpenMeteoError(f"Unexpected answer: {err!r}") from err
    return tuple(sorted(points, key=lambda point: point.start))


async def async_create_client(hass: HomeAssistant) -> OpenMeteoClient:
    """A client on Home Assistant's shared session, named after this integration."""
    integration = await async_get_integration(hass, DOMAIN)
    return OpenMeteoClient(
        async_get_clientsession(hass), f"{DOMAIN}/{integration.version}"
    )
