"""The overrides on the GX: which ones the integration wants, and releasing the rest.

The integration owns the overrides only when it has taken over the AC Battery,
which is when it publishes Grid Power. While it does not, another system may own
them and nothing is ever written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .house import House

SETPOINT_PATH = "hub4/0/Overrides/Setpoint"
MAX_DISCHARGE_PATH = "hub4/0/Overrides/MaxDischargePower"
RELEASE_SETPOINT = None
"""Written to release the setpoint override."""
RELEASE_MAX_DISCHARGE = -1
"""Written to release the discharge override; the GX then reads null."""
CONFIRM_WAIT = 10.0
"""Seconds to wait for the GX to show a release before writing it again."""


@dataclass
class WantedOverrides:
    """The overrides the integration wants right now; ``None`` means none.

    Grid Charging fills ``setpoint`` and the Discharge Block fills
    ``max_discharge_power`` (in W). Whatever is ``None`` here and set on the GX
    is released by ``OverrideRelease``.
    """

    setpoint: float | None = None
    max_discharge_power: float | None = None


class OverrideRelease:
    """Releases overrides left set on the GX that nothing wants."""

    def __init__(self, house: House) -> None:
        """Watch the House's GX."""
        self._house = house
        self._written: dict[str, float] = {}
        """When a release was last written, per path."""
        self._unsubs: list[CALLBACK_TYPE] = []

    def start(self) -> None:
        """Follow the GX, the publishing switch and the clock."""
        gateway = self._house.gateway
        if gateway is None:
            return
        self._unsubs = [
            gateway.async_add_listener(self.evaluate),
            async_track_time_interval(
                self._house.hass, self._tick, timedelta(seconds=CONFIRM_WAIT)
            ),
        ]
        publisher = self._house.grid_publisher
        if publisher is not None:
            self._unsubs.append(publisher.async_add_listener(self.evaluate))
        self.evaluate()

    def stop(self) -> None:
        """Stop following."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    async def _tick(self, _now: object) -> None:
        self.evaluate()

    def evaluate(self) -> None:
        """Release what is set on the GX and wanted by nobody."""
        house = self._house
        publisher = house.grid_publisher
        if house.gateway is None or publisher is None or not publisher.enabled:
            return
        state = house.gateway.state
        wanted = house.wanted_overrides
        self._release(
            SETPOINT_PATH,
            state.setpoint_override is not None and wanted.setpoint is None,
            RELEASE_SETPOINT,
        )
        self._release(
            MAX_DISCHARGE_PATH,
            state.max_discharge_override is not None
            and wanted.max_discharge_power is None,
            RELEASE_MAX_DISCHARGE,
        )

    def _release(self, path: str, needed: bool, value: float | None) -> None:
        if not needed:
            self._written.pop(path, None)
            return
        now = dt_util.utcnow().timestamp()
        last = self._written.get(path)
        if last is not None and now - last < CONFIRM_WAIT:
            return
        assert self._house.gateway is not None
        if self._house.gateway.async_write(path, value):
            self._written[path] = now
