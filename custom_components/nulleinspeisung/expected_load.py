"""The Expected Load of one House.

Samples the House's consumption, keeps a history per finished quarter-hour
across restarts and provides the Expected Load of coming quarter-hours (see
``expected_load_model``). The sampling and storing of the history is the
``QuarterRecorder``, which the usual output of the DC Batteries uses as well; the
PV Forecast has its own, because its records carry more.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_utc_time_change,
)
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
from .quarter_recorder import SINGLE, QuarterRecorder

if TYPE_CHECKING:
    from .house import House

SAMPLE_EVERY = timedelta(seconds=30)
SLOT = timedelta(minutes=15)
KEEP_HISTORY = timedelta(days=35)
DEFAULT_FALLBACK_KWH = 10.0
"""Daily consumption in kWh assumed until a week of history exists."""
HORIZON_SLOTS = 192
"""48 hours of quarter-hours."""


def storage_key(house_unique_id: str) -> str:
    """The storage key of a House's consumption history."""
    return f"{DOMAIN}.load_history_{house_unique_id}"


class ExpectedLoad:
    """Consumption history and Expected Load of one House."""

    def __init__(self, hass: HomeAssistant, house: House) -> None:
        """Create the Expected Load; ``async_start`` makes it work."""
        self._hass = hass
        self._house = house
        self._recorder = QuarterRecorder(
            hass, storage_key(house.config.unique_id), KEEP_HISTORY
        )
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._fallback_kwh = DEFAULT_FALLBACK_KWH
        self._profile = self._learn(dt_util.utcnow())

    # -- what later features read ----------------------------------------

    @property
    def history(self) -> tuple[LoadRecord, ...]:
        """The recorded quarter-hours, oldest first."""
        return self._recorder.history()

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
        now = dt_util.utcnow()
        await self._recorder.async_load(now)
        self._recorder.start(now)
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
        await self._recorder.async_save(force=True)

    def _learn(self, now: datetime) -> LoadProfile:
        return learn(
            self._recorder.history(),
            dt_util.get_default_time_zone(),
            fallback_watts(self._fallback_kwh),
            now,
        )

    def _recompute(self, now: datetime) -> None:
        self._profile = self._learn(now)

    # -- sampling -------------------------------------------------------------

    @callback
    def _handle_sample(self, now: datetime) -> None:
        """Take one sample of the House's consumption."""
        if self._stopped:
            return
        self._recorder.sample(now, {SINGLE: self._house.consumption()})

    async def _async_tick(self, _now: datetime | None = None) -> None:
        """A quarter-hour boundary: finish the quarter-hour, save, update."""
        if self._stopped:
            return
        now = dt_util.utcnow()
        self._recorder.roll(now)
        self._recorder.prune(now)
        self._recompute(now)
        await self._recorder.async_save()
        self._notify()
