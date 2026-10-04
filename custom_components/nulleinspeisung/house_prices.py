"""The prices of one House: fetched rarely, cached, read locally.

Tibber asks clients to fetch once a day and pick the current price from the
cache. So the stored prices are the source of truth; a fetch happens only when
they do not cover the current instant, or to pick up tomorrow's prices once
they are published.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_utc_time_change,
)
from homeassistant.util import dt as dt_util

from .alerts import raise_alert, resolve_alert
from .price_source import (
    PricePoint,
    TibberAuthError,
    TibberClient,
    TibberConnectionError,
    current_price,
    known_until,
)

_LOGGER = logging.getLogger(__name__)

RETRY_UNCOVERED = timedelta(minutes=5)
"""Wait before asking again while the stored prices do not cover now."""
POLL_TOMORROW = timedelta(minutes=15)
"""Wait before asking again for tomorrow's prices."""
MAX_POLL_DELAY = 300.0
"""Seconds of random delay added to every poll for tomorrow's prices."""
TOMORROW_FROM = time(13, 0)
"""Local time from which tomorrow's prices are expected."""
ALERT_AFTER = timedelta(minutes=15)
"""How long prices may be missing before a repair issue is raised."""

ISSUE_PRICES_UNAVAILABLE = "prices_unavailable"
ISSUE_TOKEN_REJECTED = "tibber_token_rejected"

ENGLISH_TITLES = {
    ISSUE_PRICES_UNAVAILABLE: "Prices unavailable for {house}",
    ISSUE_TOKEN_REJECTED: "Tibber token rejected",
}
"""Used when no translation can be loaded."""


def poll_delay() -> timedelta:
    """A fresh random delay for one poll for tomorrow's prices."""
    return timedelta(seconds=random.uniform(0, MAX_POLL_DELAY))


class HousePrices:
    """Holds a Tibber home's prices for one House and keeps them current."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TibberClient,
        home_id: str,
        house_name: str,
        house_unique_id: str,
    ) -> None:
        """Create the price store; ``async_start`` makes it work."""
        self._hass = hass
        self._entry = entry
        self._client = client
        self._home_id = home_id
        self._house_name = house_name
        self._issue_id = f"{ISSUE_PRICES_UNAVAILABLE}_{house_unique_id}"
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_wake: CALLBACK_TYPE | None = None
        self._not_before: datetime | None = None
        self._unavailable_since: datetime | None = None
        self._stopped = False
        self.prices: tuple[PricePoint, ...] = ()
        """All stored prices, sorted by start. The last good fetch is kept."""
        self.current: PricePoint | None = None
        """The price valid at the last quarter-hour update, if any."""

    @property
    def future(self) -> tuple[PricePoint, ...]:
        """The stored prices of slots that start after now."""
        now = dt_util.utcnow()
        return tuple(p for p in self.prices if p.start > now)

    @property
    def known_until(self) -> datetime | None:
        """End of the last known price slot."""
        return known_until(self.prices)

    @property
    def currency(self) -> str | None:
        """Currency of the prices, if any are known."""
        return self.prices[0].currency if self.prices else None

    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        """Call ``listener`` whenever the prices or the current price change."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    async def async_start(self) -> None:
        """Fetch once, then follow the quarter-hours. Never raises."""
        await self._async_run()
        self._unsub_tick = async_track_utc_time_change(
            self._hass, self._async_run, minute=(0, 15, 30, 45), second=0
        )

    def stop(self) -> None:
        """Stop all timers."""
        self._stopped = True
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
        self._cancel_wake()

    # -- the work ---------------------------------------------------------

    async def _async_run(self, *_: object) -> None:
        """Update the current price, fetch if due, and plan the next wake-up."""
        async with self._lock:
            if self._stopped:
                return
            now = dt_util.utcnow()
            self._refresh_current(now)
            due = self._next_fetch(now)
            if due is not None and due <= now:
                await self._async_fetch()
                now = dt_util.utcnow()
                self._refresh_current(now)
            self._check_alert(now)
            self._schedule_wake(now)
        for listener in list(self._listeners):
            listener()

    def _refresh_current(self, now: datetime) -> None:
        was_covered = self.current is not None
        self.current = current_price(self.prices, now)
        if self.current is not None:
            self._unavailable_since = None
        elif self._unavailable_since is None:
            self._unavailable_since = now
        if was_covered and self.current is None:
            # The prices just ran out: look for new ones now, not at the pace
            # of the poll for tomorrow's prices.
            self._not_before = None

    def _tomorrow_known(self, now: datetime) -> bool:
        today = dt_util.as_local(now).date()
        return any(dt_util.as_local(p.start).date() > today for p in self.prices)

    @staticmethod
    def _tomorrow_expected_from(now: datetime) -> datetime:
        local = dt_util.as_local(now)
        return datetime.combine(local.date(), TOMORROW_FROM, tzinfo=local.tzinfo)

    def _next_fetch(self, now: datetime) -> datetime | None:
        """When the next fetch is due; ``None`` when none is needed."""
        if self.current is None:
            return max(now, self._not_before) if self._not_before else now
        if self._tomorrow_known(now):
            return None
        due = self._tomorrow_expected_from(now)
        return max(due, self._not_before) if self._not_before else due

    async def _async_fetch(self) -> None:
        """Ask Tibber once; keep what we had on failure."""
        try:
            prices = await self._client.async_prices(self._home_id)
            if not prices:
                raise TibberConnectionError("No prices in the answer")
        except TibberAuthError:
            _LOGGER.warning("Tibber rejected the token")
            raise_alert(
                self._hass,
                self._entry,
                ISSUE_TOKEN_REJECTED,
                ISSUE_TOKEN_REJECTED,
                {},
                ENGLISH_TITLES[ISSUE_TOKEN_REJECTED],
            )
        except TibberConnectionError as err:
            _LOGGER.warning("Fetching prices for %s failed: %s", self._house_name, err)
        else:
            self.prices = prices
            resolve_alert(self._hass, ISSUE_TOKEN_REJECTED)
        self._plan_after_attempt(dt_util.utcnow())

    def _plan_after_attempt(self, now: datetime) -> None:
        """Decide how soon to ask again."""
        self._refresh_current(now)
        if self.current is None:
            self._not_before = now + RETRY_UNCOVERED
        elif not self._tomorrow_known(now):
            # One random delay per poll, so that polls do not line up.
            start = self._tomorrow_expected_from(now)
            base = start if now < start else now + POLL_TOMORROW
            self._not_before = base + poll_delay()
        else:
            self._not_before = None

    def _check_alert(self, now: datetime) -> None:
        since = self._unavailable_since
        if since is not None and now - since > ALERT_AFTER:
            raise_alert(
                self._hass,
                self._entry,
                self._issue_id,
                ISSUE_PRICES_UNAVAILABLE,
                {"house": self._house_name},
                ENGLISH_TITLES[ISSUE_PRICES_UNAVAILABLE],
            )
        elif since is None:
            resolve_alert(self._hass, self._issue_id)

    def _schedule_wake(self, now: datetime) -> None:
        """Wake up for the next fetch or the alert, whichever comes first."""
        self._cancel_wake()
        times = []
        due = self._next_fetch(now)
        if due is not None:
            times.append(due)
        if self._unavailable_since is not None:
            times.append(self._unavailable_since + ALERT_AFTER + timedelta(seconds=1))
        later = [t for t in times if t > now]
        if not later:
            return
        self._unsub_wake = async_track_point_in_utc_time(
            self._hass, self._async_wake, min(later)
        )

    async def _async_wake(self, _now: datetime) -> None:
        self._unsub_wake = None
        await self._async_run()

    def _cancel_wake(self) -> None:
        if self._unsub_wake:
            self._unsub_wake()
            self._unsub_wake = None
