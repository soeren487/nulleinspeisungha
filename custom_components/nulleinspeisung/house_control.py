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

from .alerts import raise_alert, resolve_alert
from .battery_priority import import_target
from .battery_watch import ISSUE_SETPOINT_CONFLICT, sync_issue
from .curtailment import ControllableInverter, ControlState, GroupDecision, decide_house
from .dtu_client import DtuAuthError, DtuConnectionError
from .failure import Assessment, FailureWatch
from .limit_split import LimitSplit, split_equally

if TYPE_CHECKING:
    from .house import House

_LOGGER = logging.getLogger(__name__)

DEFAULT_FEED_IN_SETPOINT = 0.0
DEFAULT_UPDATE_INTERVAL = 15.0
DEFAULT_TOLERANCE_BAND = 30.0
DEFAULT_LIMIT_FLOOR = 5.0
DEFAULT_MAXIMUM_CHARGE_POWER = 2100.0

HEADROOM_SUPPRESSION = 300.0
"""Seconds the battery headroom counts as zero after the House exported anyway."""

FULL_LIMIT = 100
"""Percent an Inverter is given when Curtailment is switched off."""

ON_FAILURE_HOLD = "hold"
ON_FAILURE_FULL = "full"
ON_FAILURE_OPTIONS = (ON_FAILURE_HOLD, ON_FAILURE_FULL)

ISSUE_GRID_METER = "grid_meter_failure"
ISSUE_DTUS = "dtu_failure"
ENGLISH_TITLES = {
    ISSUE_GRID_METER: "Grid Meter of House {house} delivers no data",
    ISSUE_DTUS: "No DTU of House {house} answers",
}
"""Used when no translation can be loaded."""


@dataclass(frozen=True, slots=True)
class _Controllable:
    """An assigned Inverter that can be given a limit right now."""

    serial: str
    dtu_key: str
    rated_power: float
    production: float | None
    data_age: float
    """Seconds old the reading is now, including time since the DTU refresh."""
    battery_backed: bool = False


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
        self.maximum_charge_power = DEFAULT_MAXIMUM_CHARGE_POWER
        """The highest power in W at which the House's AC Battery can charge."""
        self._headroom_suppressed_until = 0.0
        self._export_seen = False
        """The previous run exported beyond the band while headroom was positive."""
        self._update_interval = DEFAULT_UPDATE_INTERVAL
        self.state = ControlState.OFF
        self.on_failure = ON_FAILURE_HOLD
        """What to do with the Inverters in the failure state."""
        self.requested_percent: int | None = None
        """The percent the House currently asks of its PV group."""
        self.requested_percent_battery_backed: int | None = None
        """The percent the House currently asks of its Battery-backed group."""
        self._watch = FailureWatch()
        self._in_failure = False
        self._generations: dict[str, int] = {}
        """Restart generation of each DTU at the previous run."""
        self._pending: dict[str, int] = {}
        """Limits to send again after a DTU restart, by Inverter serial."""
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

    @property
    def curtailing(self) -> bool:
        """Whether the House currently asks less than 100 % of any Inverter."""
        if not self.curtailment:
            return False
        if any(
            percent is not None and percent < FULL_LIMIT
            for percent in (
                self.requested_percent,
                self.requested_percent_battery_backed,
            )
        ):
            return True
        return any(
            percent < FULL_LIMIT
            for percent in (*self._sent.values(), *self._pending.values())
        )

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

    def set_maximum_charge_power(self, watts: float) -> None:
        """Change the Maximum Charge Power; it takes effect from the next run."""
        self.maximum_charge_power = watts
        self._notify()

    def headroom_suppressed(self) -> bool:
        """Whether the battery headroom is ignored after unabsorbed export."""
        return dt_util.utcnow().timestamp() < self._headroom_suppressed_until

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
            self.requested_percent_battery_backed = None
            self._clear_failure()
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

    def _clear_failure(self) -> None:
        self._watch.reset()
        self._in_failure = False
        self._pending.clear()
        self._export_seen = False
        self._sync_issues(Assessment())
        sync_issue(self._house, ISSUE_SETPOINT_CONFLICT, False)

    def _sync_issues(self, assessment: Assessment) -> None:
        """Raise or clear the repair issue of each kind of failure."""
        house = self._house
        for kind, failed in (
            (ISSUE_GRID_METER, assessment.grid_meter),
            (ISSUE_DTUS, assessment.dtus),
        ):
            issue_id = f"{kind}_{house.config.unique_id}"
            if not failed:
                resolve_alert(house.hass, issue_id)
            elif house.entry is not None:
                raise_alert(
                    house.hass,
                    house.entry,
                    issue_id,
                    kind,
                    {"house": house.config.name},
                    ENGLISH_TITLES[kind],
                )

    def _note_restarts(self) -> None:
        """Queue the limits sent to a DTU that restarted since the last run."""
        for key, dtu in self._house.dtus.items():
            generation = dtu.restart_generation
            previous = self._generations.get(key, generation)
            self._generations[key] = generation
            if generation == previous or dtu.data is None:
                continue
            for serial in list(self._sent):
                if serial in dtu.data.inverters:
                    self._pending[serial] = self._sent.pop(serial)
                    self._sent_at.pop(serial, None)

    async def _async_step_locked(self) -> None:
        seen, self._export_seen = self._export_seen, False
        self._note_restarts()
        grid_power = self._house.grid_power()
        assessment = self._watch.assess(
            dt_util.utcnow().timestamp(),
            self._update_interval,
            grid_power,
            self._house.grid_meter_last_reported(),
            self._house.dtus_answering(),
        )
        self._sync_issues(assessment)
        if assessment.failed:
            await self._async_fail()
            return
        self._in_failure = False
        if grid_power is None:
            self.state = ControlState.NO_GRID_POWER
            await self._async_resend(self._controllable())
            return
        group = self._controllable()
        # An Inverter we cannot reach may lose its non-persistent limit.
        controllable = {i.serial for i in group}
        for serial in list(self._sent):
            if serial not in controllable:
                self._sent.pop(serial)
                self._sent_at.pop(serial, None)
        now = dt_util.utcnow().timestamp()
        battery = self._house.battery
        target, capped = import_target(
            self.feed_in_setpoint, battery.grid_setpoint if battery else None
        )
        sync_issue(self._house, ISSUE_SETPOINT_CONFLICT, capped)
        headroom = self._house.battery_headroom_raw()
        if headroom > 0 and grid_power - target < -self.tolerance_band:
            if self.headroom_suppressed() or seen:
                # The battery was said to take more, yet the House exports
                # again: it tapers without the BMS saying so.
                self._headroom_suppressed_until = now + HEADROOM_SUPPRESSION
            else:
                # A battery ramps up over seconds: give it one Update Interval.
                self._export_seen = True
                self.state = ControlState.HOLDING
                await self._async_resend(group)
                return

        def inputs(members: list[_Controllable]) -> list[ControllableInverter]:
            return [
                ControllableInverter(
                    rated_power=i.rated_power,
                    production=i.production if self._reading_is_new(i, now) else None,
                    limit=self._sent.get(
                        i.serial, self._pending.get(i.serial, FULL_LIMIT)
                    ),
                )
                for i in members
            ]

        pv_group = [i for i in group if not i.battery_backed]
        bb_group = [i for i in group if i.battery_backed]
        decision = decide_house(
            grid_power,
            -target + 0.0,
            self.tolerance_band,
            self.limit_floor,
            inputs(pv_group),
            inputs(bb_group),
            self._house.consumption(),
            0.0 if self.headroom_suppressed() else headroom,
        )
        self.state = decision.state
        if decision.state is ControlState.NO_INVERTER:
            self.requested_percent = None
            self.requested_percent_battery_backed = None
            await self._async_resend(group)
            return
        self.requested_percent = await self._async_apply(
            pv_group, decision.pv, self.requested_percent
        )
        self.requested_percent_battery_backed = await self._async_apply(
            bb_group,
            decision.battery_backed,
            self.requested_percent_battery_backed,
        )
        await self._async_resend(group)

    async def _async_apply(
        self,
        members: list[_Controllable],
        decision: GroupDecision | None,
        requested: int | None,
    ) -> int | None:
        """Split a group's allowance, send what differs; the percent it asks."""
        if decision is None:
            return None
        percents = self._split(
            decision.allowed_power,
            {i.serial: i.rated_power for i in members},
            self.limit_floor,
        )
        if percents and (requested is None or decision.changed):
            requested = round(sum(percents.values()) / len(percents))
        if decision.changed:
            for inverter in members:
                percent = percents[inverter.serial]
                if self._sent.get(inverter.serial) == percent:
                    continue
                if await self._async_send(inverter.dtu_key, inverter.serial, percent):
                    self._remember(inverter.serial, percent)
        return requested

    def _remember(self, serial: str, percent: int) -> None:
        self._sent[serial] = percent
        self._sent_at[serial] = dt_util.utcnow().timestamp()
        self._pending.pop(serial, None)

    async def _async_resend(self, group: list[_Controllable]) -> None:
        """Send again what a restarted DTU lost, to Inverters that are reachable.

        This repeats the House's current target; it is not a new decision, so
        it happens whether or not the current step is holding.
        """
        for inverter in group:
            percent = self._pending.get(inverter.serial)
            if percent is None:
                continue
            if await self._async_send(inverter.dtu_key, inverter.serial, percent):
                self._remember(inverter.serial, percent)

    async def _async_fail(self) -> None:
        """Carry out the failure state; the first run in it acts on the choice."""
        self.state = ControlState.FAILURE
        if self._in_failure:
            return
        self._in_failure = True
        if self.on_failure == ON_FAILURE_FULL:
            await self._async_release()
            self._pending.clear()
            self.requested_percent = FULL_LIMIT
            self.requested_percent_battery_backed = FULL_LIMIT

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
                            battery_backed=serial in self._house.config.battery_backed,
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
