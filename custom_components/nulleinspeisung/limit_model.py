"""The effective limit of an Inverter, modelled; no Home Assistant, time is passed in.

An Inverter does not jump to a new limit. After it acknowledged a command
(the latency) it moves its effective limit towards the commanded target at a
rate of about 0.5 % of rated power per second, in both directions, and its
output is the smaller of that limit and what its source can deliver.
"""

from __future__ import annotations

from dataclasses import dataclass

LATENCY = 5.0
"""Seconds until an Inverter starts moving towards a new target."""

DEFAULT_SLEW_RATE = 0.5
"""Percent of rated power per second the effective limit moves."""

LIMITED_MARGIN = 2.0
"""Percent of rated power below the effective limit that still counts as limited."""

FULL = 100.0


@dataclass(frozen=True, slots=True)
class LimitState:
    """The effective limit of one Inverter and the target it moves to."""

    effective: float = FULL
    """Effective limit in percent at ``set_at``."""
    target: float = FULL
    """Percent last commanded."""
    set_at: float = 0.0
    """Time the target was set, in seconds."""

    def effective_at(self, now: float, slew_rate: float) -> float:
        """The effective limit in percent at ``now``."""
        moving = now - self.set_at - LATENCY
        if moving <= 0:
            return self.effective
        travel = moving * slew_rate
        if self.target >= self.effective:
            return min(self.effective + travel, self.target)
        return max(self.effective - travel, self.target)

    def retarget(self, now: float, target: float, slew_rate: float) -> LimitState:
        """The state after a command for ``target`` was sent at ``now``."""
        return LimitState(self.effective_at(now, slew_rate), target, now)

    def pending_change(
        self,
        now: float,
        rated_power: float,
        production: float | None,
        slew_rate: float,
        reading_age: float = 0.0,
    ) -> float:
        """What the output will still change by because of the command sent, in W.

        Negative for a lowering still on its way, positive for a raising.
        ``reading_age`` is how many seconds old the production reading is: an
        Inverter counts as limited if its output reached the effective limit
        of the time it was measured.
        """
        effective = self.effective_at(now, slew_rate)
        now_w = effective / 100 * rated_power
        measured_w = self.effective_at(now - reading_age, slew_rate) / 100 * rated_power
        final_w = self.target / 100 * rated_power
        if self.target < effective:
            if production is None:
                return final_w - now_w
            return min(production, final_w) - production
        if self.target > effective:
            if production is None:
                return final_w - now_w
            if production >= measured_w - LIMITED_MARGIN / 100 * rated_power:
                return final_w - production
            return 0.0
        return 0.0


def ceiling(production: float | None, rated_power: float, reserve: float) -> float:
    """The highest percent worth giving an Inverter: its output plus ``reserve``.

    100 when the production is unknown.
    """
    if production is None or rated_power <= 0:
        return FULL
    return min(production / rated_power * 100 + reserve, FULL)


def next_ceiling(previous: float | None, fresh: float, reserve: float) -> float:
    """The ceiling to apply: the previous one unless ``fresh`` moved far enough.

    It moves only when the fresh ceiling differs by more than half the reserve,
    which keeps small changes of the output from causing radio traffic.
    """
    if previous is None or abs(fresh - previous) > reserve / 2:
        return fresh
    return previous
