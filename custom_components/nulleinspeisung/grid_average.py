"""The time-weighted mean of Grid Power over a window.

No Home Assistant, no I/O. Time is passed in, in seconds. A reading holds
until the next one arrives, and the last one until ``now``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence


def window_mean(
    samples: Sequence[tuple[float, float]],
    now: float,
    window: float,
    since: float | None = None,
) -> tuple[float | None, float]:
    """The mean over the last ``window`` seconds, and how much of it is covered.

    ``samples`` are (time, value) in time order. The window is covered from the
    first reading on: a single reading older than the window covers all of it,
    since the value holds until a new one arrives. Readings after ``now`` are
    ignored. ``since`` shortens the window to start no earlier than that time,
    for readings from before a change that has since taken effect would
    otherwise be averaged in; the coverage still counts against the whole
    window. Returns ``(None, 0.0)`` without a reading; when the first reading
    is at ``now`` the mean is that reading and the coverage 0.
    """
    known = [(t, v) for t, v in samples if t <= now]
    if not known or window <= 0:
        return None, 0.0
    covered_from = max(known[0][0], now - window, since or 0.0)
    span = now - covered_from
    if span <= 0:
        return known[-1][1], 0.0
    total = 0.0
    for index, (time, value) in enumerate(known):
        end = known[index + 1][0] if index + 1 < len(known) else now
        start = max(time, covered_from)
        if end > start:
            total += value * (end - start)
    return total / span, span / window


class GridPowerWindow:
    """The recent readings of the Grid Meter."""

    def __init__(self) -> None:
        """Start empty."""
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, time: float, value: float, keep: float) -> None:
        """Take a reading; forget what no window of ``keep`` seconds needs."""
        if self._samples and time < self._samples[-1][0]:
            return
        if self._samples and time == self._samples[-1][0]:
            self._samples.pop()
        self._samples.append((time, value))
        # The newest reading older than the window still holds into it.
        while len(self._samples) > 1 and self._samples[1][0] <= time - keep:
            self._samples.popleft()

    def clear(self) -> None:
        """Forget everything: the meter went away."""
        self._samples.clear()

    def mean(
        self, now: float, window: float, since: float | None = None
    ) -> tuple[float | None, float]:
        """See ``window_mean``."""
        return window_mean(self._samples, now, window, since)
