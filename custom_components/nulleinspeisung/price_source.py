"""Price source: quarter-hour prices with Price Level from a Tibber home."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import DOMAIN, TIBBER_REQUEST_TIMEOUT, TIBBER_URL

DEFAULT_SLOT = timedelta(minutes=15)
"""Length of a price slot when the data cannot tell (fewer than two points)."""

HOMES_QUERY = "{ viewer { homes { id appNickname timeZone } } }"

_PRICE_FIELDS = "total startsAt currency level"


def prices_query(home_id: str) -> str:
    """The query for today's and tomorrow's quarter-hour prices of one home."""
    return (
        f"{{ viewer {{ home(id: {json.dumps(home_id)}) {{ currentSubscription {{ "
        f"priceInfo(resolution: QUARTER_HOURLY) {{ "
        f"today {{ {_PRICE_FIELDS} }} tomorrow {{ {_PRICE_FIELDS} }} "
        f"}} }} }} }} }}"
    )


class TibberAuthError(Exception):
    """Tibber refused the token."""


class TibberConnectionError(Exception):
    """Tibber did not answer, or answered something unusable."""


class PriceLevel(StrEnum):
    """Tibber's rating of a price relative to surrounding prices."""

    VERY_CHEAP = "very_cheap"
    CHEAP = "cheap"
    NORMAL = "normal"
    EXPENSIVE = "expensive"
    VERY_EXPENSIVE = "very_expensive"


@dataclass(frozen=True, slots=True)
class TibberHome:
    """A home of the Tibber account."""

    id: str
    nickname: str


@dataclass(frozen=True, slots=True)
class PricePoint:
    """The price of one slot, starting at ``start``."""

    start: datetime
    """Timezone-aware start of the slot."""
    total: float
    """Price per kWh including taxes, in ``currency``."""
    currency: str
    level: PriceLevel


def _utc(moment: datetime) -> datetime:
    """The instant in UTC.

    Datetimes of one zone with a daylight-saving fold compare and subtract by
    their wall clock; in UTC every comparison is of real time.
    """
    return moment.astimezone(UTC)


def slot_length(prices: Sequence[PricePoint]) -> timedelta:
    """Length of one slot, taken from the data (the smallest step between starts)."""
    steps = [_utc(b.start) - _utc(a.start) for a, b in pairwise(prices)]
    steps = [step for step in steps if step > timedelta(0)]
    return min(steps) if steps else DEFAULT_SLOT


def current_price(prices: Sequence[PricePoint], now: datetime) -> PricePoint | None:
    """The price valid at ``now``, or ``None`` when the prices do not cover it.

    That is the last point starting at or before ``now``, provided ``now`` is
    less than one slot after its start.
    """
    instant = _utc(now)
    candidate = None
    for point in prices:
        if _utc(point.start) <= instant:
            candidate = point
        else:
            break
    if candidate is None or instant - _utc(candidate.start) >= slot_length(prices):
        return None
    return candidate


def known_until(prices: Sequence[PricePoint]) -> datetime | None:
    """The end of the last known price slot (in UTC), ``None`` without prices."""
    if not prices:
        return None
    return _utc(prices[-1].start) + slot_length(prices)


class TibberClient:
    """Asks the Tibber API for homes and prices, using a shared HTTP session."""

    def __init__(self, session: aiohttp.ClientSession, token: str, user_agent: str):
        """Create a client for the account of ``token``."""
        self._session = session
        self._headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent,
        }

    async def async_homes(self) -> list[TibberHome]:
        """The homes of the account."""
        data = await self._async_query(HOMES_QUERY)
        try:
            return [
                TibberHome(id=str(home["id"]), nickname=str(home["appNickname"]))
                for home in data["viewer"]["homes"]
            ]
        except (KeyError, TypeError) as err:
            raise TibberConnectionError(f"Unexpected answer: {err!r}") from err

    async def async_prices(self, home_id: str) -> tuple[PricePoint, ...]:
        """Today's, then tomorrow's prices of a home, sorted by start."""
        data = await self._async_query(prices_query(home_id))
        try:
            info = data["viewer"]["home"]["currentSubscription"]["priceInfo"]
            points = [
                _parse_point(item)
                for day in ("today", "tomorrow")
                for item in info[day]
            ]
        except (KeyError, TypeError, ValueError) as err:
            raise TibberConnectionError(f"Unexpected answer: {err!r}") from err
        return tuple(sorted(points, key=lambda point: _utc(point.start)))

    async def _async_query(self, query: str) -> dict[str, Any]:
        """Run a GraphQL query and return its ``data``."""
        try:
            async with self._session.post(
                TIBBER_URL,
                json={"query": query},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=TIBBER_REQUEST_TIMEOUT),
            ) as response:
                response.raise_for_status()
                answer = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TibberConnectionError(str(err) or type(err).__name__) from err
        if not isinstance(answer, dict):
            raise TibberConnectionError("Unexpected answer")
        errors = answer.get("errors")
        if errors:
            codes = {
                (error.get("extensions") or {}).get("code")
                for error in errors
                if isinstance(error, dict)
            }
            if "UNAUTHENTICATED" in codes:
                raise TibberAuthError("The token was rejected")
            raise TibberConnectionError(
                f"Tibber reported errors: {sorted(map(str, codes))}"
            )
        data = answer.get("data")
        if not isinstance(data, dict):
            raise TibberConnectionError("Answer without data")
        return data


def _parse_point(item: dict[str, Any]) -> PricePoint:
    """Read one price item; raises ``ValueError``/``KeyError``/``TypeError``."""
    start = datetime.fromisoformat(item["startsAt"])
    if start.tzinfo is None:
        raise ValueError("startsAt without UTC offset")
    total = item["total"]
    if isinstance(total, bool) or not isinstance(total, int | float):
        raise TypeError("total is not a number")
    return PricePoint(
        start=start,
        total=float(total),
        currency=str(item["currency"]),
        level=PriceLevel[item["level"]],
    )


async def async_create_client(hass: HomeAssistant, token: str) -> TibberClient:
    """A client on Home Assistant's shared session, named after this integration."""
    integration = await async_get_integration(hass, DOMAIN)
    return TibberClient(
        async_get_clientsession(hass), token, f"{DOMAIN}/{integration.version}"
    )
