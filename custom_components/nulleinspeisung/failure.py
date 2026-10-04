"""The failure decision of a House: is its control still fed with data?

No Home Assistant, no I/O; the time is passed in. A failure is only declared
after the data has been missing for three Update Intervals, so a single missed
reading is not one.
"""

from __future__ import annotations

from dataclasses import dataclass

GRACE_INTERVALS = 3
"""Update Intervals without data after which a House has failed."""


@dataclass(frozen=True, slots=True)
class Assessment:
    """What is wrong with a House's inputs right now."""

    grid_meter: bool = False
    """The Grid Meter delivers no data."""
    dtus: bool = False
    """None of the DTUs of the House's Inverters answers."""

    @property
    def failed(self) -> bool:
        """Whether the House is in the failure state."""
        return self.grid_meter or self.dtus


class FailureWatch:
    """Remembers when the House's inputs were last good."""

    def __init__(self) -> None:
        """Create a watch that has seen nothing yet."""
        self._meter_good: float | None = None
        self._dtu_good: float | None = None
        self.since: float | None = None
        """Seconds (same clock as ``now``) at which the failure was found."""

    def reset(self) -> None:
        """Forget everything, for when the control starts afresh."""
        self._meter_good = None
        self._dtu_good = None
        self.since = None

    def assess(
        self,
        now: float,
        update_interval: float,
        grid_power: float | None,
        last_reported: float | None,
        dtus_answering: bool | None,
    ) -> Assessment:
        """Judge the inputs of one run.

        ``grid_power`` is ``None`` when it is unknown. ``last_reported`` is when
        the Grid Meter's sensor last reported, even with an unchanged value.
        ``dtus_answering`` is ``None`` when the House has no DTU to ask about.
        """
        grace = GRACE_INTERVALS * update_interval
        if grid_power is not None:
            self._meter_good = min(last_reported, now) if last_reported else now
        elif self._meter_good is None:
            self._meter_good = now
        if dtus_answering is None or dtus_answering or self._dtu_good is None:
            self._dtu_good = now
        assessment = Assessment(
            grid_meter=now - self._meter_good >= grace,
            dtus=now - self._dtu_good >= grace,
        )
        if not assessment.failed:
            self.since = None
        elif self.since is None:
            self.since = now
        return assessment
