"""Repair issues about a House's AC Battery that do not depend on Curtailment."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .alerts import raise_alert, resolve_alert

if TYPE_CHECKING:
    from .house import House

STALE_AFTER = 120.0
"""Seconds without a fresh state after which the AC Battery counts as silent."""
CHECK_SECONDS = 15.0

ISSUE_BATTERY_STALE = "battery_not_answering"
ISSUE_DYNAMIC_ESS = "dynamic_ess_active"
ISSUE_SETPOINT_CONFLICT = "feed_in_conflicts_battery_setpoint"
ENGLISH_TITLES = {
    ISSUE_BATTERY_STALE: "AC Battery of House {house} does not answer",
    ISSUE_DYNAMIC_ESS: "Dynamic ESS is active on the AC Battery of House {house}",
    ISSUE_SETPOINT_CONFLICT: (
        "Feed-in Setpoint of House {house} asks for more import than the "
        "AC Battery's grid setpoint"
    ),
}
"""Used when no translation can be loaded."""


def sync_issue(house: House, kind: str, raised: bool) -> None:
    """Raise or clear one of the House's AC Battery repair issues."""
    issue_id = f"{kind}_{house.config.unique_id}"
    if not raised:
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


class BatteryWatch:
    """Raises issues for a silent AC Battery and for active Dynamic ESS."""

    def __init__(self, house: House) -> None:
        """Watch the AC Battery of this House."""
        self._house = house
        self._silent_since: float | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    def start(self) -> None:
        """Follow the gateway and the clock."""
        gateway = self._house.gateway
        if gateway is None:
            return
        self._silent_since = dt_util.utcnow().timestamp()
        self._unsubs = [
            gateway.async_add_listener(self.evaluate),
            async_track_time_interval(
                self._house.hass,
                self._tick,
                timedelta(seconds=CHECK_SECONDS),
            ),
        ]

    def stop(self) -> None:
        """Stop following."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    async def _tick(self, _now: object) -> None:
        self.evaluate()

    def evaluate(self) -> None:
        """Bring the issues in line with the AC Battery's state."""
        state = self._house.battery
        if state is None:
            return
        now = dt_util.utcnow().timestamp()
        if state.fresh:
            self._silent_since = None
        elif self._silent_since is None:
            self._silent_since = now
        silent = self._silent_since is not None and now - self._silent_since >= (
            STALE_AFTER
        )
        sync_issue(self._house, ISSUE_BATTERY_STALE, silent)
        if state.dynamic_ess_mode is not None:
            sync_issue(self._house, ISSUE_DYNAMIC_ESS, state.dynamic_ess_mode != 0)
