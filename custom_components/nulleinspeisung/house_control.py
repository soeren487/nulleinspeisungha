"""The control loop of one House: Curtailment of its Inverters.

Owns the owner's settings for the House, runs every Update Interval while
Curtailment is on, and sends Inverter Limits. The decisions themselves are in
``curtailment`` and ``limit_split``; entities only show and change what is
kept here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .curtailment import ControllableInverter, ControlState, decide
from .dtu_client import DtuAuthError, DtuConnectionError
from .limit_split import LimitSplit, split_equally

if TYPE_CHECKING:
    from .house import House

_LOGGER = logging.getLogger(__name__)

DEFAULT_FEED_IN_SETPOINT = 0.0
DEFAULT_UPDATE_INTERVAL = 15.0
DEFAULT_TOLERANCE_BAND = 30.0
DEFAULT_LIMIT_FLOOR = 5.0

FULL_LIMIT = 100
"""Percent an Inverter is given when Curtailment is switched off."""


@dataclass(frozen=True, slots=True)
class _Controllable:
    """An assigned Inverter that can be given a limit right now."""

    serial: str
    dtu_key: str
    rated_power: float
    production: float | None
    data_age: float
    """Seconds old the reading is now, including time since the DTU refresh."""


class HouseControl:
    """Settings, state and control loop of one House."""

    def __init__(self, house: House, split: LimitSplit = split_equally) -> None:
        """Create the control, switched off, with the default settings."""
        self._house = house
        self._split = split
        self.curtailment = False
        self.feed_in_setpoint = DEFAULT_FEED_IN_SETPOINT
        self.tolerance_band = DEFAULT_TOLERANCE_BAND
        self.limit_floor = DEFAULT_LIMIT_FLOOR
        self._update_interval = DEFAULT_UPDATE_INTERVAL
        self.state = ControlState.OFF
        self.requested_percent: int | None = None
        """The percent the House currently asks of its group."""
        self._sent: dict[str, int] = {}
        self._sent_at: dict[str, float] = {}
        """Seconds (Home Assistant clock) at which each limit was sent."""
        self._started = False
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    # -- observers ---------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` when state or settings change; returns the remover."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- settings ----------------------------------------------------------

    @property
    def update_interval(self) -> float:
        """Seconds between two adjustments."""
        return self._update_interval

    def set_update_interval(self, seconds: float) -> None:
        """Change the Update Interval; a running loop follows at once."""
        self._update_interval = seconds
        if self._unsub_timer is not None:
            self._schedule()

    # -- life cycle --------------------------------------------------------

    def start(self) -> None:
        """Begin once the restored settings are in place."""
        self._started = True
        if self.curtailment:
            self.state = ControlState.HOLDING
            self._schedule()
            self._house.hass.async_create_task(self.async_step())
        self._notify()

    def stop(self) -> None:
        """Stop the loop without sending anything."""
        self._started = False
        self._cancel()

    async def async_set_curtailment(self, on: bool) -> None:
        """Switch Curtailment on, or off and give the Inverters back 100 %."""
        if on == self.curtailment:
            return
        if on:
            self.curtailment = True
            self.state = ControlState.HOLDING
            self._notify()
            if self._started:
                self._schedule()
                await self.async_step()
            return
        self.curtailment = False
        self._cancel()
        async with self._lock:
            await self._async_release()
            self.state = ControlState.OFF
            self.requested_percent = None
        self._notify()

    def _schedule(self) -> None:
        self._cancel()
        self._unsub_timer = async_track_time_interval(
            self._house.hass,
            self._async_tick,
            timedelta(seconds=self._update_interval),
        )

    def _cancel(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_tick(self, _now: object) -> None:
        await self.async_step()

    # -- one run -----------------------------------------------------------

    async def async_step(self) -> None:
        """One adjustment of the Inverter Limits."""
        if self._lock.locked():
            return
        async with self._lock:
            if self.curtailment:
                await self._async_step_locked()
        self._notify()

    async def _async_step_locked(self) -> None:
        grid_power = self._house.grid_power()
        if grid_power is None:
            self.state = ControlState.NO_GRID_POWER
            return
        group = self._controllable()
        # An Inverter we cannot reach may lose its non-persistent limit.
        controllable = {i.serial for i in group}
        for serial in list(self._sent):
            if serial not in controllable:
                self._sent.pop(serial)
                self._sent_at.pop(serial, None)
        now = dt_util.utcnow().timestamp()
        decision = decide(
            grid_power,
            self.feed_in_setpoint,
            self.tolerance_band,
            self.limit_floor,
            [
                ControllableInverter(
                    rated_power=i.rated_power,
                    production=i.production if self._reading_is_new(i, now) else None,
                    limit=self._sent.get(i.serial, FULL_LIMIT),
                )
                for i in group
            ],
        )
        self.state = decision.state
        if decision.allowed_power is None:
            self.requested_percent = None
            return
        percents = self._split(
            decision.allowed_power,
            {i.serial: i.rated_power for i in group},
            self.limit_floor,
        )
        if percents and (
            self.requested_percent is None or decision.state is not ControlState.HOLDING
        ):
            self.requested_percent = round(sum(percents.values()) / len(percents))
        if decision.state is ControlState.HOLDING:
            return
        for inverter in group:
            percent = percents[inverter.serial]
            if self._sent.get(inverter.serial) == percent:
                continue
            if await self._async_send(inverter.dtu_key, inverter.serial, percent):
                self._sent[inverter.serial] = percent
                self._sent_at[inverter.serial] = dt_util.utcnow().timestamp()

    def _reading_is_new(self, inverter: _Controllable, now: float) -> bool:
        """Whether the production was measured after the last limit was sent."""
        sent_at = self._sent_at.get(inverter.serial)
        return sent_at is None or inverter.data_age < now - sent_at

    def _controllable(self) -> list[_Controllable]:
        """Assigned Inverters whose DTU answers, that are reachable and rated."""
        found: list[_Controllable] = []
        dtu_now = dt_util.utcnow()
        for serial in self._house.config.inverters:
            for key, dtu in self._house.dtus.items():
                if not dtu.last_update_success or dtu.data is None:
                    continue
                inverter = dtu.data.inverters.get(serial)
                if inverter is None:
                    continue
                if inverter.reachable and inverter.rated_power:
                    refreshed = getattr(dtu, "last_update_success_time", None)
                    since_refresh = (
                        max((dtu_now - refreshed).total_seconds(), 0.0)
                        if refreshed is not None
                        else 0.0
                    )
                    found.append(
                        _Controllable(
                            serial=serial,
                            dtu_key=key,
                            rated_power=inverter.rated_power,
                            production=inverter.power,
                            data_age=inverter.data_age + since_refresh,
                        )
                    )
                break
        return found

    async def _async_release(self) -> None:
        """Give every reachable assigned Inverter 100 % and forget what was sent."""
        for serial in self._house.config.inverters:
            for key, dtu in self._house.dtus.items():
                if not dtu.last_update_success or dtu.data is None:
                    continue
                inverter = dtu.data.inverters.get(serial)
                if inverter is None:
                    continue
                if inverter.reachable:
                    await self._async_send(key, serial, FULL_LIMIT)
                break
        self._sent.clear()
        self._sent_at.clear()

    async def _async_send(self, dtu_key: str, serial: str, percent: int) -> bool:
        """Send one limit; a failure is logged and reported as ``False``."""
        client = self._house.dtus[dtu_key].client
        try:
            await client.async_set_limit(serial, percent)
        except (DtuConnectionError, DtuAuthError) as err:
            _LOGGER.warning("Could not set limit %d %% on %s: %s", percent, serial, err)
            return False
        return True
