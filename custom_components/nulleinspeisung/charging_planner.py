"""The Charging Plan: which quarter-hours to charge the AC Battery from the grid in.

A pure decision. No Home Assistant, no I/O; the time is passed in. The prices
come as anything that has ``start``, ``total`` and ``level`` (a ``PricePoint``
does); the slot length is taken from the data.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Protocol

DEFAULT_SLOT = timedelta(minutes=15)
"""Length of a slot when the data cannot tell (fewer than two points)."""
REFERENCE_WINDOW = timedelta(hours=24)
"""How far after the deadline the Reference Price looks."""
_EPSILON = 1e-9


class PriceSlot(Protocol):
    """What the planner needs to know of one price slot."""

    @property
    def start(self) -> datetime:
        """Timezone-aware start of the slot."""

    @property
    def total(self) -> float:
        """Price per kWh."""

    @property
    def level(self) -> Any:
        """The Price Level; compared with the qualifying ones by equality."""


class PlanReason(StrEnum):
    """Why the plan looks as it does."""

    CHARGING_PLANNED = "charging_planned"
    TARGET_REACHED = "target_reached"
    NO_PRICES = "no_prices"
    NO_QUALIFYING_SLOT = "no_qualifying_slot"
    BLOCKED_BY_EFFICIENCY = "blocked_by_efficiency"


@dataclass(frozen=True, slots=True)
class PlannedSlot:
    """One quarter-hour (or whatever the slot length is) to charge in."""

    start: datetime
    end: datetime
    price: float
    level: Any
    stored_kwh: float
    """Energy that ends up in the battery if charged here at full power."""
    drawn_kwh: float
    """Energy drawn from the grid if charged here at full power."""


@dataclass(frozen=True, slots=True)
class ChargingPlan:
    """The outcome of one planning run."""

    slots: tuple[PlannedSlot, ...]
    """The picked slots, sorted by start."""
    energy_missing: float
    """kWh the battery still has to take to reach the target."""
    energy_to_buy: float
    """kWh to draw from the grid."""
    reference_price: float | None
    reachable: bool
    """Whether the picked slots cover the energy missing."""
    reason: PlanReason

    def is_due(self, now: datetime) -> bool:
        """Whether ``now`` lies inside a picked slot."""
        return any(slot.start <= now < slot.end for slot in self.slots)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def slot_length(prices: Sequence[PriceSlot]) -> timedelta:
    """The smallest positive step between two starts, else a quarter-hour."""
    steps = [
        step
        for a, b in pairwise(sorted(prices, key=lambda p: _utc(p.start)))
        if (step := _utc(b.start) - _utc(a.start)) > timedelta(0)
    ]
    return min(steps) if steps else DEFAULT_SLOT


def discharge_block_active(
    now: datetime,
    prices: Sequence[PriceSlot],
    qualifying: Collection[Any],
) -> bool:
    """Whether a Discharge Block is active at ``now``.

    True when the price point valid at ``now`` (``start <= now < start + slot
    length``) is known and its Price Level is one of ``qualifying``. Whether
    charging is planned, the charge level and the Battery Efficiency play no part.
    """
    if not prices:
        return False
    now = _utc(now)
    length = slot_length(prices)
    return any(
        _utc(p.start) <= now < _utc(p.start) + length and p.level in qualifying
        for p in prices
    )


def plan_charging(
    now: datetime,
    prices: Sequence[PriceSlot],
    qualifying: Collection[Any],
    charge_level: float,
    capacity_kwh: float,
    charge_target: float,
    max_charge_power: float,
    deadline: datetime,
    efficiency: float,
    ignore_efficiency: bool = False,
    max_energy: float | None = None,
) -> ChargingPlan:
    """The cheapest qualifying slots between ``now`` and ``deadline``.

    ``charge_level`` and ``charge_target`` are percent, ``max_charge_power`` is
    in W, ``efficiency`` is the round trip as a fraction. ``max_energy`` limits
    the energy to store in kWh. The slot starting before ``deadline`` and
    ending after ``now`` are the horizon; the slot ``now`` is in counts with
    what remains of it.
    """
    now, deadline = _utc(now), _utc(deadline)
    ordered = sorted(prices, key=lambda p: _utc(p.start))
    length = slot_length(ordered)
    reference = _reference_price(ordered, qualifying, deadline)

    missing = max(capacity_kwh * (charge_target - charge_level) / 100.0, 0.0)
    if max_energy is not None:
        missing = min(missing, max(max_energy, 0.0))
    if missing <= _EPSILON:
        return ChargingPlan((), 0.0, 0.0, reference, True, PlanReason.TARGET_REACHED)

    horizon = [
        p for p in ordered if _utc(p.start) + length > now and _utc(p.start) < deadline
    ]
    if not horizon:
        return ChargingPlan((), missing, 0.0, reference, False, PlanReason.NO_PRICES)

    candidates = [p for p in horizon if p.level in qualifying]
    if not candidates:
        return ChargingPlan(
            (), missing, 0.0, reference, False, PlanReason.NO_QUALIFYING_SLOT
        )
    if not ignore_efficiency and reference is not None:
        worth = efficiency * reference
        candidates = [p for p in candidates if p.total <= worth + _EPSILON]
        if not candidates:
            return ChargingPlan(
                (), missing, 0.0, reference, False, PlanReason.BLOCKED_BY_EFFICIENCY
            )

    root = math.sqrt(efficiency)
    options = []
    for point in candidates:
        start = _utc(point.start)
        remaining = min((start + length - now) / length, 1.0)
        hours = length.total_seconds() / 3600.0 * remaining
        drawn = max_charge_power * hours / 1000.0
        options.append((point.total, start, point, drawn, drawn * root))
    options.sort(key=lambda o: (o[0], o[1]))

    picked: list[PlannedSlot] = []
    stored = 0.0
    drawn_total = 0.0
    for _, start, point, drawn, kept in options:
        if stored >= missing - _EPSILON or kept <= 0:
            break
        picked.append(
            PlannedSlot(
                start=start,
                end=start + length,
                price=point.total,
                level=point.level,
                stored_kwh=kept,
                drawn_kwh=drawn,
            )
        )
        stored += kept
        drawn_total += drawn
    if not picked:
        return ChargingPlan(
            (), missing, 0.0, reference, False, PlanReason.NO_QUALIFYING_SLOT
        )
    picked.sort(key=lambda s: _utc(s.start))
    return ChargingPlan(
        slots=tuple(picked),
        energy_missing=missing,
        energy_to_buy=min(drawn_total, missing / root),
        reference_price=reference,
        reachable=stored >= missing - _EPSILON,
        reason=PlanReason.CHARGING_PLANNED,
    )


def _reference_price(
    ordered: Sequence[PriceSlot],
    qualifying: Collection[Any],
    deadline: datetime,
) -> float | None:
    """Mean price of the known non-qualifying slots in the day after the deadline."""
    totals = [
        p.total
        for p in ordered
        if deadline <= _utc(p.start) < deadline + REFERENCE_WINDOW
        and p.level not in qualifying
    ]
    return sum(totals) / len(totals) if totals else None
