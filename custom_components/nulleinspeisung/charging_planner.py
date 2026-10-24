"""The Charging Plan: which quarter-hours to charge the AC Battery from the grid in.

A pure decision. No Home Assistant, no I/O; the time is passed in.

The planner walks through the quarter-hours from now to the end of the known
prices (at most 48 hours) and follows the content of the AC Battery. In a
quarter-hour that does not qualify for charging the House cannot choose its
price: when the battery cannot carry the load there, the missing energy is a
*shortfall* at that quarter-hour's price. Shortfalls are covered, earliest
first, by charging in the cheapest qualifying quarter-hours before them whose
price, after the Battery Efficiency, is at most the shortfall's price.

Energies are in kWh, powers in W, the Battery Efficiency is the round trip as a
fraction. ``e`` below is its square root: the share that survives one half of the
trip (charging or discharging). The battery's content is measured in energy
stored above the minimum charge level (the floor).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Any, Protocol

DEFAULT_SLOT = timedelta(minutes=15)
"""Length of a price slot when the data cannot tell (fewer than two points)."""
SLOT = timedelta(minutes=15)
"""Length of one planned quarter-hour."""
SLOT_HOURS = 0.25
MAX_HORIZON = timedelta(hours=48)
"""How far ahead the planner looks at most."""
_EPSILON = 1e-6
_PROGRESS = 1e-6
"""kWh a step must remove from the shortfalls to count as progress."""
_FIT = 1e-9
"""kWh of rounding allowed when a content is compared with the Charge Target."""
_MIN_EFFICIENCY = 0.01
"""A Battery Efficiency below this is taken as this (no division by zero)."""
TOP_UP_LEVEL = 95.0
"""Percent the battery must reach on a day for BatteryLife to lower its minimum."""


class PriceSlot(Protocol):
    """What ``discharge_block_active`` needs to know of one price slot."""

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
    NO_SHORTFALL = "no_shortfall"
    BLOCKED_BY_EFFICIENCY = "blocked_by_efficiency"
    NO_QUALIFYING_SLOT = "no_qualifying_slot"
    NO_PRICES = "no_prices"
    AT_TARGET = "at_target"


@dataclass(frozen=True, slots=True)
class Quarter:
    """What the planner knows of one quarter-hour."""

    start: datetime
    """Timezone-aware start."""
    price: float
    """Price per kWh."""
    qualifies: bool
    """Whether its Price Level qualifies for charging."""
    load_w: float
    """Expected Load."""
    pv_w: float = 0.0
    """The counted PV Forecast (the share already applied); 0 where unknown."""
    support_w: float = 0.0
    """DC Battery Support."""


@dataclass(frozen=True, slots=True)
class BatteryModel:
    """The AC Battery and the owner's settings for charging it."""

    capacity_kwh: float
    """Usable capacity."""
    charge_level: float
    """Percent now."""
    floor: float
    """Minimum charge level in percent; nothing below it is usable."""
    charge_target: float
    """Percent."""
    max_charge_power: float
    """W."""
    efficiency: float
    """Round trip, as a fraction."""
    ignore_efficiency: bool = False
    """Whether the worth test is skipped."""
    discharge_blocks: bool = True
    """Whether Discharge Blocks apply: the battery is held in qualifying quarters."""


@dataclass(frozen=True, slots=True)
class TopUp:
    """The BatteryLife Top-up: charge up to ``level`` before ``end``."""

    level: float
    """Percent to reach."""
    end: datetime
    """Timezone-aware end of the window (the end of the current local day)."""


@dataclass(frozen=True, slots=True)
class PlannedSlot:
    """One quarter-hour to charge in."""

    start: datetime
    end: datetime
    price: float
    grid_kwh: float
    """Energy drawn from the grid if charged here."""
    stored_kwh: float
    """Energy that ends up in the battery."""


@dataclass(frozen=True, slots=True)
class ChargingPlan:
    """The outcome of one planning run."""

    slots: tuple[PlannedSlot, ...]
    """The planned quarter-hours, sorted by start."""
    energy_to_buy: float
    """kWh to draw from the grid in all planned quarter-hours."""
    top_up_kwh: float
    """kWh of it planned for the BatteryLife Top-up; 0 without one."""
    buffer_kwh: float
    """kWh of it in the current quarter-hour that only the Safety Buffer makes the
    plan buy; 0 when the plan would charge there anyway or no buffer was given."""
    shortfall: float
    """kWh the House would have to take from the grid at prices that do not
    qualify, without any charging."""
    uncovered: float
    """What is left of the shortfall after the planned charging, in kWh."""
    reference_price: float | None
    """Mean price of the shortfall before planning, weighted by energy."""
    energy_missing: float
    """kWh the battery could still take to reach the Charge Target."""
    battery_at_sunrise: float | None
    """kWh above the floor in the battery at the sunrise given (else at the end of
    the horizon), with the planned charging; ``None`` when no sunrise was given
    or nothing could be planned."""
    reason: PlanReason

    def is_due(self, now: datetime) -> bool:
        """Whether ``now`` lies inside a planned slot."""
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


@dataclass(frozen=True, slots=True)
class _Run:
    """The battery followed through all quarters with some charging planned."""

    shortfall: list[float]
    """kWh missing at the meter per quarter, at non-qualifying quarters only."""
    before: list[float]
    """Content at the start of each quarter."""
    end: float
    """Content after the last quarter."""


class _Model:
    """The battery model of one planning run: the fixed inputs and the simulation."""

    def __init__(
        self,
        now: datetime,
        quarters: Sequence[Quarter],
        b: BatteryModel,
        running: datetime | None = None,
    ):
        self.quarters = quarters
        self.running = next(
            (
                i
                for i, q in enumerate(quarters)
                if running is not None
                and _utc(q.start) == _utc(running)
                and _utc(q.start) <= now < _utc(q.start) + SLOT
            ),
            None,
        )
        """Index of the quarter being charged in, which keeps its place in the plan."""
        self.b = b
        self.efficiency = min(max(b.efficiency, _MIN_EFFICIENCY), 1.0)
        self.root = math.sqrt(self.efficiency)
        self.hours = [
            SLOT_HOURS * min(max((_utc(q.start) + SLOT - now) / SLOT, 0.0), 1.0)
            for q in quarters
        ]
        floor = min(max(b.floor, 0.0), 100.0)
        self.full = b.capacity_kwh * (100.0 - floor) / 100.0
        """Content when the battery is full."""
        self.target = min(
            max(b.capacity_kwh * (b.charge_target - floor) / 100.0, 0.0), self.full
        )
        """Content at the Charge Target."""
        self.start = min(
            max(b.capacity_kwh * (b.charge_level - floor) / 100.0, 0.0), self.full
        )
        """Content now; nothing below the floor counts."""

    def max_grid(self, i: int) -> float:
        """The most energy that can be drawn from the grid in quarter ``i``."""
        return self.b.max_charge_power * self.hours[i] / 1000.0

    def run(self, planned: Sequence[float]) -> _Run:
        """Follow the content through every quarter; ``planned`` is grid kWh."""
        b, root = self.b, self.root
        content = self.start
        before: list[float] = []
        shortfall: list[float] = []
        for i, q in enumerate(self.quarters):
            before.append(content)
            hours = self.hours[i]
            net = (q.load_w - q.pv_w - q.support_w) * hours / 1000.0
            missing = 0.0
            if planned[i] > 0.0:
                # The House's own net load comes from the grid: the quarter
                # qualifies, so that is no shortfall.
                content = min(content + planned[i] * root, self.full)
            elif q.qualifies and b.discharge_blocks:
                # Held: PV surplus may charge the battery, a deficit is bought.
                if net < 0.0:
                    content = min(content - net * root, self.full)
            elif net > 0.0:
                delivered = min(net, content * root)
                content -= delivered / root
                if not q.qualifies:
                    missing = net - delivered
            elif net < 0.0:
                content = min(content - net * root, self.full)
            shortfall.append(missing if missing > _EPSILON else 0.0)
        return _Run(shortfall, before, content)


def plan_charging(
    now: datetime,
    quarters: Sequence[Quarter],
    battery: BatteryModel,
    sunrise: datetime | None = None,
    running: datetime | None = None,
    top_up: TopUp | None = None,
    buffer: float | None = None,
) -> ChargingPlan:
    """Plan the charging that spares the House from buying at dear prices.

    ``quarters`` are the quarter-hours from the one ``now`` is in, in order; the
    first counts with what remains of it, those ending before ``now`` or starting
    48 hours or more after it are left out. In each:

    * a planned quarter: the battery gains the planned grid energy times ``e``,
      never above the Charge Target; the House's load comes from the grid.
    * a qualifying quarter not planned, with Discharge Blocks: the battery is
      held, PV surplus (PV plus DC Battery Support minus load) charges it times
      ``e`` up to its capacity, a deficit comes from the grid.
    * any other quarter: the load minus PV and support is delivered by the
      battery, taking that divided by ``e`` from its content down to the floor;
      what it cannot deliver is a shortfall at this quarter's price if the quarter
      does not qualify. A negative net load charges the battery times ``e``.

    The planning is greedy and deterministic. Take the earliest shortfall that is
    still open. Among the qualifying quarters before it (and after any shortfall
    already given up on before it) that can still take energy and whose price is
    at most the efficiency times the shortfall's price (unless
    ``ignore_efficiency``), buy in the cheapest; on equal prices the later one,
    because energy that is stored for less time is less exposed to a change of the
    plan. Buy what covers the shortfall (the shortfall divided by the efficiency,
    since a kWh bought delivers ``e`` times ``e`` later), limited by the quarter's
    remaining charging capacity and the Charge Target. The quarter-hour ``running``
    (its start; the one ``now`` is in and charging goes on in) is tried first, so
    that a replan inside it does not push the charging out. Repeat. A shortfall for
    which no step helps is given up on and stays uncovered.

    A ``top_up`` is planned first, before the shortfalls, so that they see the
    energy it stores. It charges in the cheapest qualifying quarter-hours that
    start before its end, without the efficiency test, within the Maximum Charge
    Power and the Charge Target, until the content reaches the top-up level at some
    point of the window. It is skipped when the Charge Target lies below that level.

    ``buffer`` (the Safety Buffer, a fraction of the Maximum Charge Power held
    back; ``None`` for none) adds two pessimistic runs, both with the Maximum Charge
    Power reduced by the buffer and the candidates taken latest first whatever
    they cost (the running quarter-hour still first); they ask when charging must
    start at the latest. (1) The top-up alone, without any PV. (2) The top-up and
    the shortfalls with the counted PV as given: for the shortfalls the buffer is
    a time margin at reduced power, not a bet against the forecast (that would
    force charging every night before an expensive day). When the larger of the
    two puts energy into the current quarter-hour, the real plan gets that
    quarter-hour (the pessimistic energy scaled back to the full power, within the
    Charge Target) before it is planned as above; the rest of the plan stays the
    normal one. The forced energy counts to ``top_up_kwh`` when run (1) gives the
    larger amount or the top-up phase of run (2) used the quarter-hour, else to the
    shortfalls; ``buffer_kwh`` is
    what it adds to the quarter-hour compared with the plan without the buffer (and
    without the place a running quarter-hour keeps).
    """
    now = _utc(now)
    horizon = now + MAX_HORIZON
    series = sorted(
        (q for q in quarters if _utc(q.start) + SLOT > now and _utc(q.start) < horizon),
        key=lambda q: _utc(q.start),
    )
    missing_to_target = max(
        battery.capacity_kwh * (battery.charge_target - battery.charge_level) / 100.0,
        0.0,
    )
    if not series:
        return ChargingPlan(
            slots=(),
            energy_to_buy=0.0,
            top_up_kwh=0.0,
            buffer_kwh=0.0,
            shortfall=0.0,
            uncovered=0.0,
            reference_price=None,
            energy_missing=missing_to_target,
            battery_at_sunrise=None,
            reason=PlanReason.NO_PRICES,
        )

    model = _Model(now, series, battery, running)
    n = len(series)
    first = model.run([0.0] * n)
    total_before = sum(first.shortfall)
    reference = (
        sum(s * series[i].price for i, s in enumerate(first.shortfall)) / total_before
        if total_before > _EPSILON
        else None
    )

    wanted: float | None = None
    top_end = _utc(top_up.end) if top_up is not None else now
    if top_up is not None:
        floor = min(max(battery.floor, 0.0), 100.0)
        level = max(battery.capacity_kwh * (top_up.level - floor) / 100.0, 0.0)
        if level <= model.target + _FIT:
            wanted = level

    preset = 0.0
    forced_by_shortfall = False
    if buffer is not None and _utc(series[0].start) <= now:
        preset, forced_by_shortfall = _buffer_start(
            model, series, battery, buffer, wanted, top_end, running, now
        )
    planned = [0.0] * n
    if preset > _EPSILON:
        planned[0] = preset
    top_up_kwh, final = _plan_all(model, planned, wanted, top_end, latest=False)
    if forced_by_shortfall:
        top_up_kwh = max(top_up_kwh - preset, 0.0)

    buffer_kwh = 0.0
    if preset > _EPSILON:
        # Without the running quarter-hour's place: that is kept by an earlier
        # plan, and it may have been the buffer that put it there.
        plain = [0.0] * n
        _plan_all(
            _Model(now, series, battery, None), plain, wanted, top_end, latest=False
        )
        buffer_kwh = max(planned[0] - plain[0], 0.0)
        if buffer_kwh <= _EPSILON:
            buffer_kwh = 0.0

    slots = tuple(
        PlannedSlot(
            start=_utc(series[i].start),
            end=_utc(series[i].start) + SLOT,
            price=series[i].price,
            grid_kwh=grid,
            stored_kwh=grid * model.root,
        )
        for i, grid in enumerate(planned)
        if grid > _EPSILON
    )
    uncovered = sum(final.shortfall)
    at_sunrise = _content_at(series, final, sunrise) if sunrise is not None else None

    return ChargingPlan(
        slots=slots,
        energy_to_buy=sum(planned),
        top_up_kwh=top_up_kwh,
        buffer_kwh=buffer_kwh,
        shortfall=total_before,
        uncovered=uncovered,
        reference_price=reference,
        energy_missing=missing_to_target,
        battery_at_sunrise=at_sunrise,
        reason=_reason(model, series, first, slots),
    )


def _plan_all(
    model: _Model,
    planned: list[float],
    wanted: float | None,
    end: datetime,
    latest: bool,
) -> tuple[float, _Run]:
    """Plan the top-up (``wanted`` is its content, ``None`` for none), then the
    shortfalls, in ``planned`` in place. Returns the energy in ``planned`` after
    the top-up and the final run."""
    top_up_kwh = 0.0
    if wanted is not None:
        _plan_top_up(model, planned, wanted, end, latest)
        top_up_kwh = sum(planned)
    run = model.run(planned)
    if sum(run.shortfall) > _EPSILON:
        run = _cover(model, planned, run, latest)
    return top_up_kwh, run


def _buffer_start(
    model: _Model,
    series: Sequence[Quarter],
    battery: BatteryModel,
    buffer: float,
    wanted: float | None,
    end: datetime,
    running: datetime | None,
    now: datetime,
) -> tuple[float, bool]:
    """What the two pessimistic runs put into the current quarter-hour (the larger),
    as grid kWh at the real power, and whether it came from the shortfalls (else
    the top-up)."""
    share = min(max(buffer, 0.0), 0.99)
    reduced = replace(
        battery, max_charge_power=battery.max_charge_power * (1.0 - share)
    )
    n = len(series)
    from_top_up = 0.0
    if wanted is not None:
        # Run 1: the top-up alone, without any PV.
        dark = _Model(now, [replace(q, pv_w=0.0) for q in series], reduced, running)
        planned = [0.0] * n
        _plan_top_up(dark, planned, wanted, end, latest=True)
        from_top_up = planned[0]
    # Run 2: top-up and shortfalls with the counted PV, as a time margin.
    counted = _Model(now, series, reduced, running)
    planned = [0.0] * n
    top_phase = 0.0
    if wanted is not None:
        _plan_top_up(counted, planned, wanted, end, latest=True)
        top_phase = planned[0]
    run = counted.run(planned)
    if sum(run.shortfall) > _EPSILON:
        _cover(counted, planned, run, latest=True)
    from_both = planned[0]
    biggest = max(from_top_up, from_both)
    if biggest <= _EPSILON:
        return 0.0, False
    scaled = min(model.max_grid(0), biggest / (1.0 - share))
    amount = _feasible(model, [0.0] * n, 0, scaled)
    by_top_up = from_top_up >= from_both - _EPSILON or top_phase > _EPSILON
    return amount, not by_top_up


def _order(model: _Model, latest: bool) -> Callable[[int], tuple[bool, float, int]]:
    """The sort key of the candidate quarters: the running one first, then the
    cheapest (the later one on equal prices), or with ``latest`` the latest."""
    series = model.quarters

    def key(j: int) -> tuple[bool, float, int]:
        return (j != model.running, 0.0 if latest else series[j].price, -j)

    return key


def _content_at(series: Sequence[Quarter], run: _Run, sunrise: datetime) -> float:
    """The content at the start of the first quarter at or after ``sunrise``.

    That is at most a quarter-hour later than the sunrise itself. After the
    last quarter the content at the end of the horizon.
    """
    sunrise = _utc(sunrise)
    for i, quarter in enumerate(series):
        if _utc(quarter.start) >= sunrise:
            return run.before[i]
    return run.end


def _reason(
    model: _Model,
    series: Sequence[Quarter],
    first: _Run,
    slots: tuple[PlannedSlot, ...],
) -> PlanReason:
    if slots:
        return PlanReason.CHARGING_PLANNED
    shortfalls = [i for i, s in enumerate(first.shortfall) if s > 0.0]
    if not shortfalls:
        return PlanReason.NO_SHORTFALL
    last = shortfalls[-1]
    qualifying = [j for j in range(last) if series[j].qualifies]
    if not qualifying:
        return PlanReason.NO_QUALIFYING_SLOT
    b = model.b
    if not b.ignore_efficiency and not any(
        series[j].price <= model.efficiency * series[s].price + _EPSILON
        for s in shortfalls
        for j in qualifying
        if j < s
    ):
        return PlanReason.BLOCKED_BY_EFFICIENCY
    return PlanReason.AT_TARGET


def _peak(run: _Run, window: int) -> float:
    """The highest content in the first ``window`` quarters: at the start and after
    each of them."""
    after = run.before[window] if window < len(run.before) else run.end
    return max([*run.before[:window], after])


def _plan_top_up(
    model: _Model,
    planned: list[float],
    content: float,
    end: datetime,
    latest: bool = False,
) -> None:
    """Plan charging (in ``planned``, in place) until the content reaches
    ``content`` at some point before ``end``, or no step helps."""
    series = model.quarters
    window = sum(1 for q in series if _utc(q.start) < end)
    run = model.run(planned)
    # Every step that helps raises the peak by at least _PROGRESS and fills a
    # quarter, hits the target or reaches the level; the bound is a second guard.
    for _ in range(8 * len(series) + 16):
        peak = _peak(run, window)
        if peak >= content - _FIT:
            return
        # Same order as for a shortfall, but every price is worth it: what is
        # bought is usable capacity, not energy for a later price.
        candidates = sorted(
            (
                j
                for j in range(window)
                if series[j].qualifies and planned[j] < model.max_grid(j) - _EPSILON
            ),
            key=_order(model, latest),
        )
        for j in candidates:
            room = model.max_grid(j) - planned[j]
            amount = _feasible(
                model, planned, j, min(room, (content - peak) / model.root)
            )
            if amount <= _EPSILON:
                continue
            planned[j] += amount
            trial = model.run(planned)
            if _peak(trial, window) > peak + _PROGRESS:
                run = trial
                break
            planned[j] -= amount
        else:
            return


def _cover(
    model: _Model, planned: list[float], first: _Run, latest: bool = False
) -> _Run:
    """Plan charging (in ``planned``, in place) for the shortfalls; the final run."""
    n = len(model.quarters)
    run = first
    given_up: set[int] = set()
    # Every step removes at least _PROGRESS from the shortfalls or gives one
    # shortfall up; there are at most ``n`` of them, and every step that helps
    # fills a quarter, hits the target or covers a shortfall. The bound is a
    # second guard against endless loops.
    for _ in range(8 * n + 16):
        open_shortfalls = [
            i for i, s in enumerate(run.shortfall) if s > 0.0 and i not in given_up
        ]
        if not open_shortfalls:
            break
        target = open_shortfalls[0]
        run = _cover_one(model, planned, run, target, given_up, latest) or run
    return model.run(planned)


def _cover_one(
    model: _Model,
    planned: list[float],
    run: _Run,
    target: int,
    given_up: set[int],
    latest: bool = False,
) -> _Run | None:
    """Buy for the shortfall at ``target``; ``None`` (and given up) when it fails."""
    series = model.quarters
    b = model.b
    after = max((k for k in given_up if k < target), default=-1)
    worth = model.efficiency * series[target].price
    candidates = [
        j
        for j in range(after + 1, target)
        if series[j].qualifies
        and planned[j] < model.max_grid(j) - _EPSILON
        and (b.ignore_efficiency or series[j].price <= worth + _EPSILON)
    ]
    # The quarter-hour being charged in comes first, whatever the others cost: a
    # plan that moved on to a later equal or cheaper quarter-hour in the middle of
    # a quarter-hour would stop the charging and start it again. Then the
    # cheapest first; on equal prices the later one. With ``latest`` the price
    # is ignored: the latest first.
    candidates.sort(key=_order(model, latest))
    wanted = run.shortfall[target] / model.efficiency
    before_total = sum(run.shortfall)
    for j in candidates:
        room = model.max_grid(j) - planned[j]
        amount = _feasible(model, planned, j, min(wanted, room))
        if amount <= _EPSILON:
            continue
        planned[j] += amount
        trial = model.run(planned)
        if sum(trial.shortfall) < before_total - _PROGRESS:
            return trial
        planned[j] -= amount
    given_up.add(target)
    return None


def _feasible(model: _Model, planned: list[float], j: int, amount: float) -> float:
    """The most of ``amount`` that can be added at ``j`` without any planned
    quarter ending above the Charge Target."""
    if amount <= _EPSILON:
        return 0.0
    if _within_target(model, planned, j, amount):
        return amount
    low, high = 0.0, amount
    for _ in range(40):
        middle = (low + high) / 2.0
        if _within_target(model, planned, j, middle):
            low = middle
        else:
            high = middle
    return low


def _within_target(model: _Model, planned: list[float], j: int, amount: float) -> bool:
    planned[j] += amount
    try:
        run = model.run(planned)
    finally:
        planned[j] -= amount
    limit = model.target + _FIT
    return all(
        run.before[k] + (planned[k] + (amount if k == j else 0.0)) * model.root <= limit
        for k in range(len(planned))
        if planned[k] > 0.0 or k == j
    )
