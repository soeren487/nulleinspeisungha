"""The overrides on the GX: which ones the integration wants, and keeping the GX so.

The integration owns the overrides only when it has taken over the AC Battery,
which is when it publishes Grid Power. While it does not, another system may own
them and nothing is written, with one exception: an override the integration set
itself is released when it loses the battery.
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
"""Seconds to wait for the GX to show a write before writing the same again."""
_SAME = 0.5
"""W below which a value on the GX counts as the wanted one."""


@dataclass
class WantedOverrides:
    """The overrides the integration wants right now; ``None`` means none.

    Grid Charging fills ``setpoint`` and the Discharge Block fills
    ``max_discharge_power`` (in W). ``OverrideControl`` writes what is wanted
    and releases whatever is ``None`` here and set on the GX.
    """

    setpoint: float | None = None
    max_discharge_power: float | None = None


class OverrideControl:
    """Writes the wanted overrides to the GX and releases the unwanted ones."""

    def __init__(self, house: House) -> None:
        """Watch the House's GX."""
        self._house = house
        self._written: dict[str, tuple[float, float | None]] = {}
        """When and what was last written, per path."""
        self._ours: set[str] = set()
        """Paths where the integration set a wanted value and has not released it."""
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
        """Bring the overrides on the GX in line with the wanted ones."""
        house = self._house
        publisher = house.grid_publisher
        if house.gateway is None or publisher is None:
            return
        state = house.gateway.state
        wanted = house.wanted_overrides
        owned = publisher.enabled
        self._sync(
            SETPOINT_PATH,
            state.setpoint_override,
            wanted.setpoint,
            RELEASE_SETPOINT,
            owned,
        )
        self._sync(
            MAX_DISCHARGE_PATH,
            state.max_discharge_override,
            wanted.max_discharge_power,
            RELEASE_MAX_DISCHARGE,
            owned,
        )

    def _sync(
        self,
        path: str,
        current: float | None,
        wanted: float | None,
        release: float | None,
        owned: bool,
    ) -> None:
        if not owned:
            # Another system may own the overrides: only take back our own.
            if path in self._ours and current is not None:
                self._write(path, release)
            else:
                self._ours.discard(path)
                self._written.pop(path, None)
            return
        if wanted is None:
            needed = current is not None
            value = release
        else:
            needed = current is None or abs(current - wanted) >= _SAME
            value = wanted
        if not needed:
            self._written.pop(path, None)
            if wanted is None:
                self._ours.discard(path)
            return
        if self._write(path, value) and wanted is not None:
            self._ours.add(path)

    def _write(self, path: str, value: float | None) -> bool:
        """Write ``value`` unless the same value was written a moment ago."""
        now = dt_util.utcnow().timestamp()
        last = self._written.get(path)
        if last is not None and last[1] == value and now - last[0] < CONFIRM_WAIT:
            return False
        assert self._house.gateway is not None
        if self._house.gateway.async_write(path, value):
            self._written[path] = (now, value)
            return True
        return False
