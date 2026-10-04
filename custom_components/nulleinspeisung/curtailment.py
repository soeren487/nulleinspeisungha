"""The Curtailment controller: one pure decision per House and Update Interval.

No Home Assistant, no I/O. Grid Power is positive for import; the Feed-in
Setpoint is positive for export, so the Grid Power to aim for is its negation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class ControlState(StrEnum):
    """What the control is doing, for display."""

    OFF = "off"
    HOLDING = "holding"
    LOWERING = "lowering"
    RAISING = "raising"
    NO_GRID_POWER = "no_grid_power"
    NO_INVERTER = "no_inverter"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class ControllableInverter:
    """An Inverter that can be given a limit right now."""

    rated_power: float
    """Rated power in W."""
    production: float | None
    """Current production in W; ``None`` if unknown or not newer than the
    House's last limit change."""
    limit: float
    """Percent the House last gave it; 100 if it has not given it one."""


@dataclass(frozen=True, slots=True)
class Decision:
    """The controller's answer for one group of Inverters."""

    state: ControlState
    """``HOLDING``, ``LOWERING``, ``RAISING`` or ``NO_INVERTER``."""
    allowed_power: float | None
    """Production the group is allowed in W; ``None`` if nothing can be done."""


def decide(
    grid_power: float,
    feed_in_setpoint: float,
    tolerance_band: float,
    floor_percent: float,
    inverters: Sequence[ControllableInverter],
) -> Decision:
    """Decide the allowed production of one group of Inverters.

    Inside the tolerance band the group's current allowance is kept. Outside
    it the allowance moves by the deviation from the target, starting from
    the sum over the Inverters of the smaller of allowance and production when
    lowering, and from the allowance when raising.
    """
    if not inverters:
        return Decision(ControlState.NO_INVERTER, None)
    rated = sum(i.rated_power for i in inverters)
    allowed = sum(i.limit / 100 * i.rated_power for i in inverters)
    deviation = grid_power - (-feed_in_setpoint)
    if abs(deviation) <= tolerance_band:
        return Decision(ControlState.HOLDING, allowed)
    if deviation < 0:
        # A limit only bites below what is really produced. An Inverter whose
        # production is unknown is assumed to produce what it is allowed.
        base = sum(
            min(
                i.limit / 100 * i.rated_power,
                i.limit / 100 * i.rated_power if i.production is None else i.production,
            )
            for i in inverters
        )
    else:
        # Readings can predate the last limit change: raise from the allowance.
        base = allowed
    floor = floor_percent / 100 * rated
    new = min(max(base + deviation, floor), rated)
    state = ControlState.LOWERING if deviation < 0 else ControlState.RAISING
    return Decision(state, new)
