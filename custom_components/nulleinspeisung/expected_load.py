"""The Expected Load of one House.

Samples the House's consumption, keeps a history per finished quarter-hour
across restarts and provides the Expected Load of coming quarter-hours (see
``expected_load_model``). It is the sibling of the PV Forecast; the two share
only ``slot_start`` because the PV Forecast's slots also carry flags and
irradiance and its records have another layout.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_utc_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .expected_load_model import (
    SLOT_HOURS,
    LoadProfile,
    LoadRecord,
    fallback_watts,
    learn,
)
from .forecast import slot_start

if TYPE_CHECKING:
    from .house import House

_LOGGER = logging.getLogger(__name__)

SAMPLE_EVERY = timedelta(seconds=30)
SLOT = timedelta(minutes=15)
MIN_COVERED = SLOT.total_seconds() / 2
"""Seconds of a quarter-hour that valid samples must cover to record it."""
MAX_SAMPLE_COVER = SAMPLE_EVERY.total_seconds()
"""Seconds one sample may cover at most."""
KEEP_HISTORY = timedelta(days=35)
SAVE_EVERY = timedelta(minutes=15)
STORAGE_VERSION = 1
DEFAULT_FALLBACK_KWH = 10.0
"""Daily consumption in kWh assumed until a week of history exists."""
HORIZON_SLOTS = 192
"""48 hours of quarter-hours."""


@dataclass
class _Slot:
    """The quarter-hour being observed."""

    start: datetime
    total: float = 0.0
    count: int = 0
    covered: float = 0.0


def storage_key(house_unique_id: str) -> str:
    """The storage key of a House's consumption history."""
    return f"{DOMAIN}.load_history_{house_unique_id}"


class ExpectedLoad:
    """Consumption history and Expected Load of one House."""

    def __init__(self, hass: HomeAssistant, house: House) -> None:
        """Create the Expected Load; ``async_start`` makes it work."""
        self._hass = hass
        self._house = house
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(house.config.unique_id)
        )
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._records: list[LoadRecord] = []
        self._dirty = False
        self._last_save: datetime | None = None
        self._slot: _Slot | None = None
        self._last_sample: datetime | None = None
        self._fallback_kwh = DEFAULT_FALLBACK_KWH
        self._profile = self._learn(dt_util.utcnow())

    # -- what later features read ----------------------------------------

    @property
    def history(self) -> tuple[LoadRecord, ...]:
        """The recorded quarter-hours, oldest first."""
        return tuple(self._records)

    @property
    def fallback_kwh(self) -> float:
        """The daily consumption in kWh used while nothing is learned."""
        return self._fallback_kwh

    def set_fallback_kwh(self, value: float) -> None:
        """Change the fallback daily consumption and update everything."""
        self._fallback_kwh = value
        self._recompute(dt_util.utcnow())
        self._notify()

    @property
    def learned_days(self) -> int:
        """Days with enough records to count."""
        return self._profile.learned_days

    @property
    def learned(self) -> bool:
        """Whether the history is long enough to use the learned values."""
        return self._profile.learned

    def at(self, start: datetime) -> float:
        """The Expected Load in W of the quarter-hour starting at ``start``."""
        return self._profile.watts_at(start)

    def upcoming(self) -> tuple[tuple[datetime, float], ...]:
        """(start, W) of the quarter-hours from the current one over 48 hours."""
        first = slot_start(dt_util.utcnow())
        return tuple(
            (start, self._profile.watts_at(start))
            for start in (first + i * SLOT for i in range(HORIZON_SLOTS))
        )

    def energy_next_24h(self) -> float:
        """Expected consumption in kWh over the next 24 hours."""
        return sum(w for _, w in self.upcoming()[:96]) * SLOT_HOURS / 1000

    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        """Call ``listener`` whenever the Expected Load may have changed."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- life cycle -------------------------------------------------------

    async def async_start(self) -> None:
        """Load the history, then sample and follow the clock."""
        await self._async_load()
        now = dt_util.utcnow()
        self._last_sample = now
        self._recompute(now)
        self._unsubs.append(
            async_track_time_interval(self._hass, self._handle_sample, SAMPLE_EVERY)
        )
        self._unsubs.append(
            async_track_utc_time_change(
                self._hass, self._async_tick, minute=(0, 15, 30, 45), second=0
            )
        )

    async def async_stop(self) -> None:
        """Stop the timers and write the history."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._async_save(force=True)

    # -- history ------------------------------------------------------------

    async def _async_load(self) -> None:
        stored = await self._store.async_load()
        records: list[LoadRecord] = []
        for row in (stored or {}).get("records", []):
            try:
                start, watts = row
                records.append(
                    LoadRecord(
                        dt_util.utc_from_timestamp(float(start)),
                        max(float(watts), 0.0),
                    )
                )
            except TypeError, ValueError:
                _LOGGER.warning("Ignoring an unreadable consumption record")
        records.sort(key=lambda record: record.start)
        self._records = records
        self._prune(dt_util.utcnow())

    async def _async_save(self, force: bool = False) -> None:
        if not self._dirty:
            return
        now = dt_util.utcnow()
        if not force and self._last_save and now - self._last_save < SAVE_EVERY:
            return
        self._dirty = False
        self._last_save = now
        await self._store.async_save(
            {"records": [[int(r.start.timestamp()), r.watts] for r in self._records]}
        )

    def _prune(self, now: datetime) -> None:
        keep = [r for r in self._records if r.start >= now - KEEP_HISTORY]
        if len(keep) != len(self._records):
            self._records = keep
            self._dirty = True

    def _learn(self, now: datetime) -> LoadProfile:
        return learn(
            self._records,
            dt_util.get_default_time_zone(),
            fallback_watts(self._fallback_kwh),
            now,
        )

    def _recompute(self, now: datetime) -> None:
        self._profile = self._learn(now)

    # -- sampling -------------------------------------------------------------

    def _roll(self, now: datetime) -> None:
        """Finish the observed quarter-hour if ``now`` is in a later one."""
        start = slot_start(now)
        slot = self._slot
        if slot is not None and slot.start >= start:
            return
        if slot is not None:
            self._finish(slot)
        self._slot = _Slot(start)

    def _finish(self, slot: _Slot) -> None:
        if slot.count == 0 or slot.covered < MIN_COVERED:
            return
        self._records.append(LoadRecord(slot.start, max(slot.total / slot.count, 0.0)))
        self._dirty = True

    @callback
    def _handle_sample(self, now: datetime) -> None:
        """Take one sample of the House's consumption."""
        if self._stopped:
            return
        now = dt_util.as_utc(now)
        self._roll(now)
        slot = self._slot
        assert slot is not None
        since = max(self._last_sample or slot.start, slot.start)
        self._last_sample = now
        consumption = self._house.consumption()
        if consumption is None:
            return
        slot.total += consumption
        slot.count += 1
        slot.covered += min((now - since).total_seconds(), MAX_SAMPLE_COVER)

    async def _async_tick(self, _now: datetime | None = None) -> None:
        """A quarter-hour boundary: finish the quarter-hour, save, update."""
        if self._stopped:
            return
        now = dt_util.utcnow()
        self._roll(now)
        self._prune(now)
        self._recompute(now)
        await self._async_save()
        self._notify()
