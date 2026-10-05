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
    """Percent the House last gave it (its target); 100 if it has not given it
    one."""
    pending_change: float = 0.0
    """Signed W the output will still change by because of the limit already
    sent: negative for a lowering still on its way."""


@dataclass(frozen=True, slots=True)
class GroupDecision:
    """The controller's answer for one of the two groups."""

    allowed_power: float
    """Production the group is allowed in W."""
    changed: bool
    """Whether this run acts on the group: its allowance moved, or the step's
    deviation reached it (so a limit may have to be sent)."""


@dataclass(frozen=True, slots=True)
class HouseDecision:
    """The controller's answer for a House with up to two groups."""

    state: ControlState
    """``HOLDING``, ``LOWERING``, ``RAISING`` or ``NO_INVERTER``."""
    pv: GroupDecision | None
    """``None`` if no PV Inverter is controllable."""
    battery_backed: GroupDecision | None
    """``None`` if no Battery-backed Inverter is controllable."""
    corrected_deviation: float = 0.0
    """Grid Power minus its target, minus the pending changes of all Inverters,
    in W; without battery headroom."""


@dataclass(frozen=True, slots=True)
class _Group:
    rated: float
    allowed: float
    base: float
    floor: float
    production: float
    """Production in W; the allowance for an Inverter without a reading."""


def _group(inverters: Sequence[ControllableInverter], floor_percent: float) -> _Group:
    rated = sum(i.rated_power for i in inverters)
    allowed = sum(i.limit / 100 * i.rated_power for i in inverters)
    # A limit only bites below what is really produced. An Inverter whose
    # production is unknown is assumed to produce what it is allowed.
    base = sum(
        min(
            i.limit / 100 * i.rated_power,
            i.limit / 100 * i.rated_power if i.production is None else i.production,
        )
        for i in inverters
    )
    production = sum(
        i.limit / 100 * i.rated_power if i.production is None else i.production
        for i in inverters
    )
    return _Group(rated, allowed, base, floor_percent / 100 * rated, production)


def decide_house(
    grid_power: float,
    feed_in_setpoint: float,
    tolerance_band: float,
    floor_percent: float,
    pv_inverters: Sequence[ControllableInverter],
    battery_backed_inverters: Sequence[ControllableInverter],
    consumption: float | None,
    battery_headroom: float = 0.0,
) -> HouseDecision:
    """Decide the allowed production of the PV group and the Battery-backed group.

    Inside the tolerance band a group's allowance is kept; outside it the
    allowance moves by the deviation. The deviation is corrected by the change
    the Inverters have still to make because of limits already sent: an export
    that a lowering on its way will remove is no deviation (but a pending change
    never turns an export into an import or the other way round). Lowering starts
    from the sum over the Inverters of the smaller of allowance and production,
    raising from the allowance. ``battery_headroom`` is the charge power the
    AC Battery could still take: it raises the deviation unless the House
    exports beyond the band.

    Lowering takes from the Battery-backed group first (its lost energy stays
    stored) and from the PV group only what remains. Raising gives to the PV
    group first; the Battery-backed group gets only what remains of the part
    of the deviation that comes from Grid Power.

    The Battery-backed group is also matched to ``consumption`` (skipped when
    it is ``None``). It is capped: when what it delivers exceeds consumption by
    more than the band, its allowance becomes consumption (not below its floor,
    not above its allowance). It is released: in a run that lowers nothing,
    with no PV group or the PV group at 100 %, a biting allowance below the
    rated power that is more than the band below consumption becomes
    consumption (at most the rated power).
    """
    if not pv_inverters and not battery_backed_inverters:
        return HouseDecision(
            ControlState.NO_INVERTER, None, None, grid_power + feed_in_setpoint
        )
    pv = _group(pv_inverters, floor_percent) if pv_inverters else None
    bb = (
        _group(battery_backed_inverters, floor_percent)
        if battery_backed_inverters
        else None
    )
    new_pv = pv.allowed if pv else 0.0
    new_bb = bb.allowed if bb else 0.0
    touched_pv = touched_bb = False

    pending = sum(i.pending_change for i in (*pv_inverters, *battery_backed_inverters))
    grid_deviation = grid_power - (-feed_in_setpoint)
    corrected = grid_deviation - pending
    # The pending change may cancel the deviation, never reverse it: a
    # production reading older than the Grid Power would otherwise turn an
    # export that is still going away into a demand to raise.
    grid_deviation = 0.0 if corrected * grid_deviation < 0 else corrected
    deviation = grid_deviation
    if deviation >= -tolerance_band:
        deviation += battery_headroom
    step = ControlState.HOLDING
    if abs(deviation) > tolerance_band:
        if deviation < 0:
            step = ControlState.LOWERING
            remaining = -deviation
            if bb:
                touched_bb = True
                taken = min(remaining, max(bb.base - bb.floor, 0.0))
                new_bb = min(max(bb.base - remaining, bb.floor), bb.rated)
                remaining -= taken
            if pv and remaining > 0:
                touched_pv = True
                new_pv = min(max(pv.base - remaining, pv.floor), pv.rated)
        else:
            step = ControlState.RAISING
            remaining = deviation
            if pv:
                touched_pv = True
                new_pv = min(pv.allowed + remaining, pv.rated)
                used = new_pv - pv.allowed
                remaining -= used
                # PV serves the import first; the headroom is no demand for
                # the Battery-backed group.
                grid_left = grid_deviation - used
            else:
                grid_left = grid_deviation
            if bb:
                share = min(remaining, max(grid_left, 0.0))
                if share > 0:
                    touched_bb = True
                    new_bb = min(bb.allowed + share, bb.rated)

    if bb and consumption is not None:
        band = tolerance_band
        if bb.production > consumption + band:
            new_bb = min(new_bb, max(consumption, bb.floor))
        elif (
            step is not ControlState.LOWERING
            and (pv is None or pv.allowed >= pv.rated)
            and new_bb < bb.rated
            and bb.production >= new_bb - band
            and consumption > new_bb + band
        ):
            new_bb = min(consumption, bb.rated)

    pv_result = (
        GroupDecision(new_pv, touched_pv or new_pv != pv.allowed) if pv else None
    )
    bb_result = (
        GroupDecision(new_bb, touched_bb or new_bb != bb.allowed) if bb else None
    )
    lowered = (pv is not None and new_pv < pv.allowed) or (
        bb is not None and new_bb < bb.allowed
    )
    state = ControlState.LOWERING if lowered else step
    return HouseDecision(state, pv_result, bb_result, grid_deviation)
