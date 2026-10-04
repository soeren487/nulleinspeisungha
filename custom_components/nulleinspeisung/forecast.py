"""The PV Forecast of one House.

Fetches the irradiance forecast, keeps the House's production history (per
finished quarter-hour, across restarts) and converts irradiance to production
with the factors learned from that history (see ``forecast_model``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_utc_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .alerts import raise_alert, resolve_alert
from .const import DOMAIN
from .forecast_model import (
    SLOT_HOURS,
    Factors,
    ForecastSlot,
    HistoryRecord,
    convert,
    history_days,
    is_usable,
    learn,
)
from .forecast_source import IrradiancePoint, OpenMeteoClient, OpenMeteoError

if TYPE_CHECKING:
    from .house import House

_LOGGER = logging.getLogger(__name__)

FETCH_EVERY = timedelta(hours=1)
"""Wait before fetching the irradiance again after a success."""
RETRY_AFTER = timedelta(minutes=10)
"""Wait before fetching again after a failure."""
ALERT_AFTER = timedelta(hours=6)
"""How old the last successful fetch may be before a repair issue is raised."""
KEEP_HISTORY = timedelta(days=60)
"""How long production records are kept."""
KEEP_IRRADIANCE = timedelta(days=2)
"""How long past irradiance is kept, so that finished slots can be recorded."""
SLOT = timedelta(minutes=15)
MIN_COVERED = SLOT.total_seconds() / 2
"""Seconds of a quarter-hour that samples must cover for it to be recorded."""
MAX_SAMPLE_COVER = 30.0
"""Seconds one sample may cover at most (a DTU delivers every 10 seconds)."""
STORAGE_VERSION = 1

ISSUE_FORECAST_UNAVAILABLE = "pv_forecast_unavailable"
ENGLISH_TITLE = "PV forecast for House {house} unavailable"
"""Used when no translation can be loaded."""


@dataclass
class _Slot:
    """The quarter-hour being observed."""

    start: datetime
    total: float = 0.0
    count: int = 0
    covered: float = 0.0
    bad: bool = False
    """A PV Inverter was unknown at some moment: the quarter-hour is not recorded."""
    curtailed: bool = False


def slot_start(moment: datetime) -> datetime:
    """The start of the quarter-hour containing ``moment``."""
    moment = dt_util.as_utc(moment)
    return moment.replace(minute=moment.minute // 15 * 15, second=0, microsecond=0)


def storage_key(house_unique_id: str) -> str:
    """The storage key of a House's production history."""
    return f"{DOMAIN}.pv_history_{house_unique_id}"


class HouseForecast:
    """Irradiance, production history and PV Forecast of one House."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OpenMeteoClient,
        house_name: str,
        house_unique_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Create the forecast; ``attach`` and ``async_start`` make it work."""
        self._hass = hass
        self._entry = entry
        self._client = client
        self._house_name = house_name
        self._latitude = latitude
        self._longitude = longitude
        self._issue_id = f"{ISSUE_FORECAST_UNAVAILABLE}_{house_unique_id}"
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(house_unique_id)
        )
        self._house: House | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_wake: CALLBACK_TYPE | None = None
        self._stopped = False
        self._started_at = dt_util.utcnow()
        self._last_success: datetime | None = None
        self._next_fetch: datetime | None = None
        self._irradiance: dict[datetime, float] = {}
        self._records: list[HistoryRecord] = []
        self._dirty = False
        self._slot: _Slot | None = None
        self._last_sample: datetime | None = None
        self.factors: Factors | None = None
        """The learned conversion; ``None`` while nothing can be learned yet."""
        self.history_days = 0
        """Days with enough records to count towards usability."""
        self.usable = False
        """Whether the history is long enough to trust the PV Forecast."""

    def attach(self, house: House) -> None:
        """Tell the forecast which House it belongs to."""
        self._house = house

    # -- what later features read ----------------------------------------

    @property
    def history(self) -> tuple[HistoryRecord, ...]:
        """The recorded quarter-hours, oldest first."""
        return tuple(self._records)

    @property
    def irradiance(self) -> tuple[IrradiancePoint, ...]:
        """The known irradiance forecast, sorted by start."""
        return tuple(IrradiancePoint(s, v) for s, v in sorted(self._irradiance.items()))

    @property
    def last_success(self) -> datetime | None:
        """When the irradiance was last fetched successfully."""
        return self._last_success

    @property
    def slots(self) -> tuple[ForecastSlot, ...]:
        """The PV Forecast of every known quarter-hour, past ones included."""
        if self.factors is None:
            return ()
        return convert(self.factors, self._irradiance.items())

    @property
    def future(self) -> tuple[ForecastSlot, ...]:
        """The PV Forecast of the known quarter-hours that start after now."""
        now = dt_util.utcnow()
        return tuple(s for s in self.slots if s.start > now)

    @property
    def current(self) -> ForecastSlot | None:
        """The PV Forecast of the quarter-hour now."""
        start = slot_start(dt_util.utcnow())
        return next((s for s in self.slots if s.start == start), None)

    def energy_kwh(self, day: date) -> float | None:
        """Expected production of a local day in kWh; ``None`` when unknown."""
        chosen = [s for s in self.slots if dt_util.as_local(s.start).date() == day]
        if not chosen:
            return None
        return sum(s.watts for s in chosen) * SLOT_HOURS / 1000

    def async_add_listener(self, listener: CALLBACK_TYPE) -> Callable[[], None]:
        """Call ``listener`` whenever the forecast or the history changes."""
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
        """Load the history, fetch once, then follow DTUs and the clock.

        Never raises: the irradiance being out of reach must not stop the entry.
        """
        assert self._house is not None
        await self._async_load()
        self._recompute(dt_util.utcnow())
        for dtu in self._house.dtus.values():
            self._unsubs.append(dtu.async_add_listener(self._handle_dtu))
        self._unsubs.append(
            self._house.control.async_add_listener(self._handle_control)
        )
        self._unsubs.append(
            async_track_utc_time_change(
                self._hass, self._async_tick, minute=(0, 15, 30, 45), second=0
            )
        )
        await self._async_run()

    async def async_stop(self) -> None:
        """Stop all timers and write the history."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_wake()
        await self._async_save()

    # -- production history -------------------------------------------------

    async def _async_load(self) -> None:
        stored = await self._store.async_load()
        records: list[HistoryRecord] = []
        for row in (stored or {}).get("records", []):
            try:
                start, production, irradiance, curtailed = row
                records.append(
                    HistoryRecord(
                        dt_util.utc_from_timestamp(float(start)),
                        float(production),
                        None if irradiance is None else float(irradiance),
                        bool(curtailed),
                    )
                )
            except TypeError, ValueError:
                _LOGGER.warning("Ignoring an unreadable history record")
        records.sort(key=lambda record: record.start)
        self._records = records
        self._prune(dt_util.utcnow())

    async def _async_save(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        await self._store.async_save(
            {
                "records": [
                    [
                        int(r.start.timestamp()),
                        r.production,
                        r.irradiance,
                        r.curtailed,
                    ]
                    for r in self._records
                ]
            }
        )

    def _prune(self, now: datetime) -> None:
        keep = [r for r in self._records if r.start >= now - KEEP_HISTORY]
        if len(keep) != len(self._records):
            self._records = keep
            self._dirty = True

    def _recompute(self, now: datetime) -> None:
        self.factors = learn(self._records, now)
        self.history_days = history_days(self._records)
        self.usable = is_usable(self._records)

    @callback
    def _handle_dtu(self) -> None:
        """A DTU delivered data: take a sample."""
        self._observe(dt_util.utcnow())

    @callback
    def _handle_control(self) -> None:
        """The control changed: remember that the House curtailed in this slot."""
        assert self._house is not None
        if self._house.control.curtailing:
            self._roll(dt_util.utcnow())
            assert self._slot is not None
            self._slot.curtailed = True

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
        if slot.bad or slot.count == 0 or slot.covered < MIN_COVERED:
            return
        self._records.append(
            HistoryRecord(
                slot.start,
                slot.total / slot.count,
                self._irradiance.get(slot.start),
                slot.curtailed,
            )
        )
        self._dirty = True
        now = dt_util.utcnow()
        self._prune(now)
        self._recompute(now)
        self._notify()

    def _observe(self, now: datetime) -> None:
        """Take one sample of the House's PV production."""
        assert self._house is not None
        self._roll(now)
        slot = self._slot
        assert slot is not None
        if self._house.control.curtailing:
            slot.curtailed = True
        production = self._house.pv_production_complete()
        if production is None:
            slot.bad = True
        else:
            slot.total += production
            slot.count += 1
        since = max(self._last_sample or slot.start, slot.start)
        slot.covered += min((now - since).total_seconds(), MAX_SAMPLE_COVER)
        self._last_sample = now

    async def _async_tick(self, _now: datetime | None = None) -> None:
        """A quarter-hour boundary: finish the quarter-hour, save, update."""
        if self._stopped:
            return
        now = dt_util.utcnow()
        assert self._house is not None
        if self._slot is not None and self._house.pv_production_complete() is None:
            self._slot.bad = True
        self._roll(now)
        self._prune(now)
        self._recompute(now)
        await self._async_save()
        self._notify()

    # -- irradiance ---------------------------------------------------------

    async def _async_run(self, *_: object) -> None:
        """Fetch if due, check the alert and plan the next wake-up."""
        async with self._lock:
            if self._stopped:
                return
            now = dt_util.utcnow()
            if self._next_fetch is None or self._next_fetch <= now:
                await self._async_fetch()
                now = dt_util.utcnow()
            self._check_alert(now)
            self._schedule_wake(now)
        self._notify()

    async def _async_fetch(self) -> None:
        """Ask Open-Meteo once; keep what we had on failure."""
        now = dt_util.utcnow()
        try:
            points = await self._client.async_irradiance(
                self._latitude, self._longitude
            )
            if not points:
                raise OpenMeteoError("No irradiance in the answer")
        except OpenMeteoError as err:
            _LOGGER.warning(
                "Fetching the irradiance for %s failed: %s", self._house_name, err
            )
            self._next_fetch = now + RETRY_AFTER
            return
        for point in points:
            self._irradiance[point.start] = point.irradiance
        self._irradiance = {
            start: value
            for start, value in self._irradiance.items()
            if start >= now - KEEP_IRRADIANCE
        }
        self._last_success = now
        self._next_fetch = now + FETCH_EVERY

    def _alert_reference(self) -> datetime:
        return self._last_success or self._started_at

    def _check_alert(self, now: datetime) -> None:
        if now - self._alert_reference() > ALERT_AFTER:
            if self._entry is not None:
                raise_alert(
                    self._hass,
                    self._entry,
                    self._issue_id,
                    ISSUE_FORECAST_UNAVAILABLE,
                    {"house": self._house_name},
                    ENGLISH_TITLE,
                )
        else:
            resolve_alert(self._hass, self._issue_id)

    def _schedule_wake(self, now: datetime) -> None:
        """Wake up for the next fetch or the alert, whichever comes first."""
        self._cancel_wake()
        times = [
            self._next_fetch,
            self._alert_reference() + ALERT_AFTER + timedelta(seconds=1),
        ]
        later = [t for t in times if t is not None and t > now]
        if later:
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
