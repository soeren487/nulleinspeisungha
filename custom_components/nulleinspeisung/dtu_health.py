"""Pure decisions about a DTU's health. No I/O; the time is always passed in."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

UNSUCCESSFUL_BEFORE_BACKOFF = 3
"""Unsuccessful restarts in a row after which the wait starts to double."""
MAX_WAIT = timedelta(hours=1)
"""Upper limit of the wait between two restarts."""


@dataclass(frozen=True, slots=True)
class InverterObservation:
    """What a DTU reports about one Inverter, as far as health is concerned."""

    data_age: float
    """Seconds since the DTU last received data from the Inverter."""
    is_pv: bool = True
    """Whether the Inverter is a PV Inverter (Battery-backed ones are ignored)."""


@dataclass(frozen=True, slots=True)
class StuckSettings:
    """Per-DTU settings of the stuck decision."""

    sun_angle: float
    """Degrees of sun elevation above which production must be possible."""
    staleness_time: float
    """Seconds after which Inverter data counts as no longer fresh."""


def is_stuck(
    observations: Iterable[InverterObservation],
    sun_elevation: float,
    settings: StuckSettings,
    *,
    other_dtu_producing: bool = False,
) -> bool:
    """Decide whether a DTU is a Stuck DTU.

    Stuck means the DTU has at least one PV Inverter, none of them has data
    younger than the staleness time, and either the sun is above the sun
    angle or ``other_dtu_producing`` (another DTU of the same House delivers
    power, which makes silence here wrong at any sun angle).
    """
    pv_ages = [o.data_age for o in observations if o.is_pv]
    if not pv_ages:
        return False
    if not all(age >= settings.staleness_time for age in pv_ages):
        return False
    return sun_elevation > settings.sun_angle or other_dtu_producing


class Action(StrEnum):
    """What the restart policy wants done."""

    NOTHING = "nothing"
    RESTART = "restart"


@dataclass(frozen=True, slots=True)
class Verdict:
    """The restart policy's answer."""

    action: Action
    not_helping: bool
    """Whether restarting has failed often enough that the owner must be told."""


class RestartPolicy:
    """Remembers restarts of one DTU and decides when the next one is due."""

    def __init__(self, wait: timedelta) -> None:
        """Create a policy that waits ``wait`` after a restart."""
        self._wait = wait
        self._wait_until: datetime | None = None
        self._unsuccessful = 0
        self._awaiting_outcome = False
        self._stuck = False

    @property
    def unsuccessful_restarts(self) -> int:
        """Restarts in a row after which the DTU was still stuck."""
        return self._unsuccessful

    def evaluate(self, now: datetime, stuck: bool) -> Verdict:
        """Say what to do now. Call :meth:`record_restart` after restarting.

        While the wait after a restart runs nothing is concluded: a restarted
        DTU reports its uptime as data age and so looks fresh for a while. The
        outcome of a restart is judged at the first call after the wait.
        """
        if self.waiting(now):
            return Verdict(Action.NOTHING, not_helping=self._not_helping)
        self._stuck = stuck
        if not stuck:
            self._unsuccessful = 0
            self._awaiting_outcome = False
            return Verdict(Action.NOTHING, not_helping=False)
        if self._awaiting_outcome:
            self._unsuccessful += 1
            self._awaiting_outcome = False
        return Verdict(Action.RESTART, not_helping=self._not_helping)

    def waiting(self, now: datetime) -> bool:
        """Whether the wait after the last restart is still running."""
        return self._wait_until is not None and now < self._wait_until

    def holding_stuck(self, now: datetime) -> bool:
        """Whether the DTU was stuck when restarted and the wait still runs.

        The caller keeps showing it as stuck: its data cannot be trusted yet.
        """
        return self._awaiting_outcome and self.waiting(now)

    def record_restart(self, now: datetime) -> None:
        """Note that a restart was attempted, automatically or by the owner.

        It starts the wait. It counts as unsuccessful only if the DTU was
        stuck at the time and is still stuck after the wait.
        """
        self._wait_until = now + self._current_wait()
        self._awaiting_outcome = self._stuck

    @property
    def _not_helping(self) -> bool:
        return self._unsuccessful >= UNSUCCESSFUL_BEFORE_BACKOFF

    def _current_wait(self) -> timedelta:
        """The wait after the next restart: doubling once restarts do not help."""
        extra = self._unsuccessful - UNSUCCESSFUL_BEFORE_BACKOFF + 1
        if extra <= 0:
            return min(self._wait, MAX_WAIT)
        return min(self._wait * 2**extra, MAX_WAIT)
