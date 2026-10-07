"""What every quarter-hour recorder shares: samples in, finished quarter-hours out.

A recorder collects samples of one or more series (a consumption, the output of
each of some Inverters) per quarter-hour. A quarter-hour becomes a record, the
mean of its valid samples, only if those samples cover at least half of it. The
records are kept for a given time in Home Assistant's storage, written at most
every quarter of an hour and when the recorder is stopped.

The owner decides what is sampled and when (it holds the timers) and what the
records mean. The PV Forecast keeps its own recorder: its records also carry
irradiance and a curtailment flag, and a sample with an unknown Inverter
spoils the whole quarter-hour.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

SLOT = timedelta(minutes=15)
MIN_COVERED = SLOT.total_seconds() / 2
"""Seconds of a quarter-hour that valid samples must cover to record it."""
MAX_SAMPLE_COVER = 30.0
"""Seconds one sample may cover at most."""
SAVE_EVERY = timedelta(minutes=15)
STORAGE_VERSION = 1
SINGLE = ""
"""The name of the series of a recorder that has only one."""


@dataclass(frozen=True, slots=True)
class QuarterRecord:
    """What one finished quarter-hour showed."""

    start: datetime
    """Timezone-aware start of the quarter-hour."""
    watts: float
    """Mean power in W; never negative."""


@dataclass
class _Slot:
    """One series in the quarter-hour being observed."""

    total: float = 0.0
    count: int = 0
    covered: float = 0.0


def quarter_start(moment: datetime) -> datetime:
    """The start (UTC) of the quarter-hour that ``moment`` lies in."""
    moment = dt_util.as_utc(moment)
    return moment.replace(minute=moment.minute // 15 * 15, second=0, microsecond=0)


class QuarterRecorder:
    """The records of one or more series, collected, kept and stored.

    With ``by_series`` the storage holds ``{"series": {name: [[start, W], ...]}}``,
    else one unnamed series as ``{"records": [[start, W], ...]}``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        storage_key: str,
        keep: timedelta,
        *,
        by_series: bool = False,
    ) -> None:
        """Create the recorder; ``async_load`` reads what was stored."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key)
        self._keep = keep
        self._by_series = by_series
        self._records: dict[str, list[QuarterRecord]] = {}
        self._dirty = False
        self._last_save: datetime | None = None
        self._slot_start: datetime | None = None
        self._slots: dict[str, _Slot] = {}
        self._last_sample: datetime | None = None

    # -- what the owner reads ---------------------------------------------

    def history(self, series: str = SINGLE) -> tuple[QuarterRecord, ...]:
        """The recorded quarter-hours of a series, oldest first."""
        return tuple(self._records.get(series, ()))

    @property
    def series(self) -> tuple[str, ...]:
        """The names of the series that have records."""
        return tuple(name for name, records in self._records.items() if records)

    # -- storage ------------------------------------------------------------

    async def async_load(self, now: datetime) -> None:
        """Read the stored records and drop the ones older than ``keep``."""
        stored = await self._store.async_load() or {}
        rows: dict[str, Any] = (
            stored.get("series", {})
            if self._by_series
            else {SINGLE: stored.get("records", [])}
        )
        records: dict[str, list[QuarterRecord]] = {}
        for name, series_rows in rows.items():
            parsed: list[QuarterRecord] = []
            for row in series_rows:
                try:
                    start, watts = row
                    parsed.append(
                        QuarterRecord(
                            dt_util.utc_from_timestamp(float(start)),
                            max(float(watts), 0.0),
                        )
                    )
                except TypeError, ValueError:
                    _LOGGER.warning("Ignoring an unreadable quarter-hour record")
            parsed.sort(key=lambda record: record.start)
            records[name] = parsed
        self._records = records
        self.prune(now)

    async def async_save(self, force: bool = False) -> None:
        """Write the records if they changed; not more often than ``SAVE_EVERY``
        unless ``force``.
        """
        if not self._dirty:
            return
        now = dt_util.utcnow()
        if not force and self._last_save and now - self._last_save < SAVE_EVERY:
            return
        self._dirty = False
        self._last_save = now
        encoded = {
            name: [[int(r.start.timestamp()), r.watts] for r in records]
            for name, records in self._records.items()
        }
        await self._store.async_save(
            {"series": encoded}
            if self._by_series
            else {"records": encoded.get(SINGLE, [])}
        )

    def prune(self, now: datetime) -> None:
        """Drop the records older than ``keep``."""
        for name, records in self._records.items():
            keep = [r for r in records if r.start >= now - self._keep]
            if len(keep) != len(records):
                self._records[name] = keep
                self._dirty = True

    # -- sampling -------------------------------------------------------------

    def start(self, now: datetime) -> None:
        """Begin sampling at ``now``."""
        self._last_sample = now

    def roll(self, now: datetime) -> None:
        """Finish the observed quarter-hour if ``now`` is in a later one."""
        start = quarter_start(now)
        if self._slot_start is not None and self._slot_start >= start:
            return
        self._finish()
        self._slot_start = start
        self._slots = {}

    def sample(self, now: datetime, values: Mapping[str, float | None]) -> None:
        """Take one sample; a series whose value is ``None`` gets none."""
        now = dt_util.as_utc(now)
        self.roll(now)
        assert self._slot_start is not None
        since = max(self._last_sample or self._slot_start, self._slot_start)
        self._last_sample = now
        covered = min((now - since).total_seconds(), MAX_SAMPLE_COVER)
        for name, value in values.items():
            if value is None:
                continue
            slot = self._slots.setdefault(name, _Slot())
            slot.total += value
            slot.count += 1
            slot.covered += covered

    def _finish(self) -> None:
        if self._slot_start is None:
            return
        for name, slot in self._slots.items():
            if slot.count == 0 or slot.covered < MIN_COVERED:
                continue
            self._records.setdefault(name, []).append(
                QuarterRecord(self._slot_start, max(slot.total / slot.count, 0.0))
            )
            self._dirty = True
