"""The usual output of a House's Battery-backed Inverters.

Samples the output of each Battery-backed Inverter, keeps a history per finished
quarter-hour across restarts and provides the usual output per quarter-hour of
the local day (see ``dc_battery``). It runs whether or not the owner has named
sensors, so that the history exists once they do.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_utc_time_change,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .dc_battery import usual_output
from .quarter_recorder import QuarterRecorder

if TYPE_CHECKING:
    from datetime import datetime

    from .house import House

SAMPLE_EVERY = timedelta(seconds=30)
KEEP_HISTORY = timedelta(days=21)


def storage_key(house_unique_id: str) -> str:
    """The storage key of a House's history of Battery-backed output."""
    return f"{DOMAIN}.dc_output_history_{house_unique_id}"


class DcBatteryHistory:
    """Output history of the Battery-backed Inverters of one House."""

    def __init__(self, hass: HomeAssistant, house: House) -> None:
        """Create the history; ``async_start`` makes it work."""
        self._hass = hass
        self._house = house
        self._recorder = QuarterRecorder(
            hass, storage_key(house.config.unique_id), KEEP_HISTORY, by_series=True
        )
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._outputs: dict[str, dict[int, float]] = {}

    @property
    def recorder(self) -> QuarterRecorder:
        """The records, for whoever wants to look at them."""
        return self._recorder

    def usual_output(self) -> Mapping[str, Mapping[int, float]]:
        """Per Battery-backed Inverter, W per slot of the local day (0 to 95)."""
        return self._outputs

    def _learn(self) -> None:
        tz = dt_util.get_default_time_zone()
        self._outputs = {
            serial: usual_output(self._recorder.history(serial), tz)
            for serial in self._house.config.battery_backed
        }

    async def async_start(self) -> None:
        """Load the history, then sample and follow the clock."""
        now = dt_util.utcnow()
        await self._recorder.async_load(now)
        self._recorder.start(now)
        self._learn()
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

    @callback
    def _handle_sample(self, now: datetime) -> None:
        """Take one sample of every Battery-backed Inverter's output."""
        if self._stopped:
            return
        self._recorder.sample(
            now,
            {
                serial: self._house.inverter_power(serial)
                for serial in self._house.config.battery_backed
            },
        )

    async def _async_tick(self, _now: datetime | None = None) -> None:
        """A quarter-hour boundary: finish the quarter-hour, save, learn."""
        if self._stopped:
            return
        now = dt_util.utcnow()
        self._recorder.roll(now)
        self._recorder.prune(now)
        self._learn()
        await self._recorder.async_save()
