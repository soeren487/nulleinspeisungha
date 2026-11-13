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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from .quarters import SLOT

SLOT_HOURS = 0.25
MAX_HORIZON = timedelta(hours=48)
"""How far ahead the planner looks at most."""
_EPSILON = 1e-6
_PROGRESS = 1e-6
"""kWh a step must remove from the shortfalls to count as progress."""
_FIT = 1e-9
"""kWh of rounding allowed when a content is compared with the Charge Target."""
_STEPS = 12
"""Steps of ``_feasible`` before it falls back to bisection."""
_MIN_EFFICIENCY = 0.01
"""A Battery Efficiency below this is taken as this (no division by zero)."""
MIN_BUFFER_POWER = 500.0
"""W below which the inverter/charger works poorly: the least the Safety Buffer
charges at."""


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
    """Whether the House may apply Discharge Blocks at all (Grid Charging is on);
    the planner then decides in which quarters the battery is held."""


@dataclass(frozen=True, slots=True)
class TopUp:
    """The BatteryLife Top-up: charge up to ``level`` before ``end``."""

    level: float
    """Percent to reach."""
    end: datetime
    """Timezone-aware end of the window: the end of the run of qualifying
    quarter-hours that goes on at midnight, else midnight (the caller decides)."""


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
    top_up_kwh: float = 0.0
    """Part of ``grid_kwh`` planned for the BatteryLife Top-up."""


@dataclass(frozen=True, slots=True)
class HeldFor:
    """The shortfall the battery is held for: what would be missing at ``start``
    (the quarter-hour's start) at ``price`` per kWh without any holding."""

    start: datetime
    kwh: float
    price: float


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
    buffer_power: float | None = None
    """W the Safety Buffer asks the House to charge at now; ``None`` when the
    buffer does not apply to the current quarter-hour."""
    held: tuple[datetime, ...] = ()
    """Starts of the qualifying quarter-hours in which the battery is held (a
    Discharge Block): those the planner holds back stored energy in for a dearer
    time, and those in which the plan charges. Empty without Discharge Blocks."""
    held_for: HeldFor | None = None
    """The first shortfall, without any holding, that lies after the first held
    quarter-hour and is dearer than it: what a hold serves; ``None`` without holds
    or when no such shortfall exists (a hold only keeps what the top-up bought)."""

    def is_due(self, now: datetime) -> bool:
        """Whether ``now`` lies inside a planned slot."""
        return any(slot.start <= now < slot.end for slot in self.slots)

    def is_held(self, now: datetime) -> bool:
        """Whether ``now`` lies inside a quarter-hour in which the battery is held."""
        return any(start <= now < start + SLOT for start in self.held)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


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
        self._net = [
            (q.load_w - q.pv_w - q.support_w) * hours / 1000.0
            for q, hours in zip(quarters, self.hours, strict=True)
        ]
        """kWh the battery would have to deliver per quarter (negative: surplus)."""
        self._qualifies = [q.qualifies for q in quarters]
        self._last: tuple[list[float], list[bool], _Run] | None = None
        """The inputs and the outcome of the last simulation."""
        self.held = [False] * len(quarters)
        """The qualifying quarters in which the battery is held (decided so far)."""
        self.top: tuple[float, int] | None = None
        """While a top-up is being planned: its content and window length. The
        quarters from the first planned one to the one that reaches the content are
        held too, whatever is planned."""

    def max_grid(self, i: int) -> float:
        """The most energy that can be drawn from the grid in quarter ``i``."""
        return self.b.max_charge_power * self.hours[i] / 1000.0

    def run(self, planned: Sequence[float]) -> _Run:
        """Follow the content through every quarter; ``planned`` is grid kWh."""
        held = self.held
        if self.top is not None:
            top = self.top_held(planned)
            held = [a or b for a, b in zip(held, top, strict=True)]
        return self._simulate(planned, held)

    def top_held(self, planned: Sequence[float]) -> list[bool]:
        """The qualifying quarters held for the top-up in ``self.top``: from the
        first planned one to the one in which the content reaches the top-up level,
        within its window. Holding there keeps the battery from discharging what
        was just bought."""
        n = len(self.quarters)
        held = [False] * n
        if self.top is None or not self.b.discharge_blocks:
            return held
        level, window = self.top
        first = next((j for j in range(window) if planned[j] > 0.0), None)
        if first is None:
            return held
        for j in range(first, window):
            held[j] = self.quarters[j].qualifies
        run = self._simulate(planned, held)
        for j in range(first, window):
            after = run.before[j + 1] if j + 1 < n else run.end
            if after >= level - _FIT:
                for k in range(j + 1, n):
                    held[k] = False
                break
        return held

    def fix_top_up(self, planned: Sequence[float]) -> None:
        """The top-up is planned: keep its held quarters for the rest of the run."""
        if self.top is not None:
            top = self.top_held(planned)
            self.held = [a or b for a, b in zip(self.held, top, strict=True)]
            self.top = None

    def _simulate(self, planned: Sequence[float], held: Sequence[bool]) -> _Run:
        """The run for ``planned`` and ``held``.

        The plan changes a little at a time, so the run resumes from the first
        quarter in which the inputs differ from those of the run before it, and
        ends as soon as the content is the one of that run again after the last
        quarter in which they differ: what lies before and after is the same
        arithmetic, not recomputed.
        """
        n = len(self.quarters)
        root, full = self.root, self.full
        nets, qualifies = self._net, self._qualifies
        blocks = self.b.discharge_blocks
        planned_now = list(planned)
        held_now = list(held)
        start = 0
        content = self.start
        before: list[float] = []
        shortfall: list[float] = []
        last = self._last
        stop = n
        run: _Run | None = None
        if last is not None:
            planned_then, held_then, run = last
            start = next(
                (
                    i
                    for i in range(n)
                    if planned_now[i] != planned_then[i] or held_now[i] != held_then[i]
                ),
                n,
            )
            if start == n:
                return run
            stop = next(
                i
                for i in range(n - 1, -1, -1)
                if planned_now[i] != planned_then[i] or held_now[i] != held_then[i]
            )
            content = run.before[start]
            before = run.before[:start]
            shortfall = run.shortfall[:start]
        for i in range(start, n):
            if i > stop and run is not None and content == run.before[i]:
                before.extend(run.before[i:])
                shortfall.extend(run.shortfall[i:])
                content = run.end
                break
            before.append(content)
            net = nets[i]
            missing = 0.0
            grid = planned_now[i]
            if grid > 0.0:
                # The House's own net load comes from the grid: the quarter
                # qualifies, so that is no shortfall.
                content = min(content + grid * root, full)
            elif held_now[i] and blocks and qualifies[i]:
                # Held: PV surplus may charge the battery, a deficit is bought.
                if net < 0.0:
                    content = min(content - net * root, full)
            elif net > 0.0:
                delivered = min(net, content * root)
                content -= delivered / root
                if not qualifies[i]:
                    missing = net - delivered
            elif net < 0.0:
                content = min(content - net * root, full)
            shortfall.append(missing if missing > _EPSILON else 0.0)
        result = _Run(shortfall, before, content)
        self._last = (planned_now, held_now, result)
        return result


def plan_charging(
    now: datetime,
    quarters: Sequence[Quarter],
    battery: BatteryModel,
    sunrise: datetime | None = None,
    running: datetime | None = None,
    top_up: TopUp | None = None,
    buffer: float | None = None,
    buffer_latched: bool = False,
) -> ChargingPlan:
    """Plan the charging that spares the House from buying at dear prices.

    ``quarters`` are the quarter-hours from the one ``now`` is in, in order; the
    first counts with what remains of it, those ending before ``now`` or starting
    48 hours or more after it are left out. In each:

    * a planned quarter: the battery gains the planned grid energy times ``e``,
      never above the Charge Target; the House's load comes from the grid.
    * a quarter in which the battery is held (a qualifying quarter, with Discharge
      Blocks, that the planner holds, see below): PV surplus (PV plus DC Battery
      Support minus load) charges it times ``e`` up to its capacity, a deficit
      comes from the grid.
    * any other quarter: the load minus PV and support is delivered by the
      battery, taking that divided by ``e`` from its content down to the floor;
      what it cannot deliver is a shortfall at this quarter's price if the quarter
      does not qualify. A negative net load charges the battery times ``e``.

    Nothing is held at first. Holding comes before buying, since energy already
    stored costs no conversion loss. For each shortfall, earliest first, the
    qualifying quarters before it whose price is lower than the shortfall's, latest
    first (the energy is held for the shortest time), are held, each kept only when
    it reduces the total shortfall, until the shortfall is gone. Quarters in which
    the plan charges are held implicitly. While a top-up is wanted, the quarters
    from the first planned top-up quarter to the one in which the content reaches
    the top-up level are held too, so that the battery does not discharge what was
    just bought. ``shortfall`` and ``reference_price`` describe what is left after
    holding, before buying. Buying then goes on with the held set, and what is
    bought in a quarter for a shortfall also holds the qualifying quarters between
    them whose price is lower than the shortfall's (the House would otherwise use
    up at a cheap price what it paid the conversion loss for).

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

    The buffer charges steadily, not at full power in every other quarter-hour:
    its power (``buffer_power``) is, for every deadline of the pessimistic plans
    (the end of the top-up window, the start of each shortfall), the energy they
    place before it divided by the qualifying hours from now to it, divided by
    ``1 - buffer``; the highest of these, at least ``MIN_BUFFER_POWER`` and at most
    the Maximum Charge Power. The current quarter-hour gets that power for what
    remains of it. With ``buffer_latched`` (the buffer charged in the quarter-hour
    before) it applies as long as the pessimistic plans still need any energy and
    the quarter-hour qualifies, so that the energy it already stored does not make
    it pause. The pessimistic runs hold the same way on their own (reduced-power)
    model, so that each is consistent with itself.
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
    probe = _Model(now, series, battery, running)
    raw = probe.run([0.0] * n)
    first = _hold(probe, [0.0] * n, raw)
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
    buffer_power: float | None = None
    if buffer is not None and _utc(series[0].start) <= now:
        preset, forced_by_shortfall, buffer_power = _buffer_start(
            model,
            series,
            battery,
            buffer,
            wanted,
            top_end,
            running,
            now,
            buffer_latched,
        )
    planned = [0.0] * n
    if preset > _EPSILON:
        planned[0] = preset
    top_up_kwh, final, top_part = _plan_all(
        model, planned, wanted, top_end, latest=False
    )
    if forced_by_shortfall:
        top_up_kwh = max(top_up_kwh - preset, 0.0)
        top_part[0] = max(top_part[0] - preset, 0.0)

    buffer_kwh = 0.0
    if preset <= _EPSILON:
        buffer_power = None
    else:
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
            top_up_kwh=min(top_part[i], grid),
        )
        for i, grid in enumerate(planned)
        if grid > _EPSILON
    )
    held = tuple(
        _utc(series[i].start)
        for i in range(n)
        if series[i].qualifies
        and battery.discharge_blocks
        and (model.held[i] or planned[i] > _EPSILON)
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
        buffer_power=buffer_power,
        held=held,
        held_for=_held_for(series, raw, held),
    )


def _held_for(
    series: Sequence[Quarter], raw: _Run, held: Sequence[datetime]
) -> HeldFor | None:
    """The first shortfall of ``raw`` (the run without any holding) after the first
    held quarter-hour that is dearer than it."""
    if not held:
        return None
    first = next(i for i, q in enumerate(series) if _utc(q.start) == held[0])
    return next(
        (
            HeldFor(_utc(series[s].start), raw.shortfall[s], series[s].price)
            for s in range(first + 1, len(series))
            if raw.shortfall[s] > _EPSILON
            and series[s].price > series[first].price + _EPSILON
        ),
        None,
    )


def _plan_all(
    model: _Model,
    planned: list[float],
    wanted: float | None,
    end: datetime,
    latest: bool,
) -> tuple[float, _Run, list[float]]:
    """Plan the top-up (``wanted`` is its content, ``None`` for none), then the
    shortfalls, in ``planned`` in place. Returns the energy in ``planned`` after
    the top-up, the final run and the energy per quarter after the top-up."""
    top_up_kwh = 0.0
    if wanted is not None:
        _plan_top_up(model, planned, wanted, end, latest)
        top_up_kwh = sum(planned)
    top_part = list(planned) if wanted is not None else [0.0] * len(planned)
    run = _hold(model, planned, model.run(planned))
    if sum(run.shortfall) > _EPSILON:
        run = _cover(model, planned, run, latest)
    return top_up_kwh, run, top_part


def _hold(model: _Model, planned: Sequence[float], run: _Run) -> _Run:
    """Hold the battery (``model.held``, in place) in qualifying quarters whose
    stored energy a dearer shortfall needs; the run with the holds.

    Earliest shortfall first; the candidates are the qualifying quarters before it
    with a lower price in which the plan does not charge, taken latest first (the
    energy is held for the shortest time). Holding more never lowers the content,
    so the shortfalls only fall as candidates are added: all candidates are tried
    and, if that reduces the total shortfall, the fewest latest ones that reach
    the same total are kept (found by bisection). No conversion loss is involved,
    so there is no efficiency test."""
    series = model.quarters
    if not model.b.discharge_blocks:
        return run
    for s in range(len(series)):
        if run.shortfall[s] <= 0.0:
            continue
        candidates = [
            j
            for j in range(s - 1, -1, -1)
            if series[j].qualifies
            and not model.held[j]
            and planned[j] <= 0.0
            and series[j].price < series[s].price - _EPSILON
        ]

        def total(count: int, candidates: list[int] = candidates) -> _Run:
            for k, j in enumerate(candidates):
                model.held[j] = k < count
            return model.run(planned)

        everything = total(len(candidates))
        goal = sum(everything.shortfall)
        if goal >= sum(run.shortfall) - _PROGRESS:
            total(0)
            continue
        low, high = 0, len(candidates)  # high reaches the goal, low does not
        while high - low > 1:
            middle = (low + high) // 2
            if sum(total(middle).shortfall) <= goal + _PROGRESS:
                high = middle
            else:
                low = middle
        run = total(high)
    return run


def _buffer_start(
    model: _Model,
    series: Sequence[Quarter],
    battery: BatteryModel,
    buffer: float,
    wanted: float | None,
    end: datetime,
    running: datetime | None,
    now: datetime,
    latched: bool = False,
) -> tuple[float, bool, float | None]:
    """What the Safety Buffer puts into the current quarter-hour, as grid kWh, whether
    that came from the shortfalls (else the top-up), and the power it charges at.

    The pessimistic runs decide whether it applies and at what power (see
    ``plan_charging``); nothing when they need nothing, or when they do not need the
    current quarter-hour and the buffer is not ``latched``."""
    share = min(max(buffer, 0.0), 0.99)
    reduced = replace(
        battery, max_charge_power=battery.max_charge_power * (1.0 - share)
    )
    n = len(series)
    window = sum(1 for q in series if _utc(q.start) < end)
    from_top_up = 0.0
    total_top_up = 0.0
    pace = 0.0
    if wanted is not None:
        # Run 1: the top-up alone, without any PV.
        dark = _Model(now, [replace(q, pv_w=0.0) for q in series], reduced, running)
        planned = [0.0] * n
        _plan_top_up(dark, planned, wanted, end, latest=True)
        from_top_up = planned[0]
        total_top_up = sum(planned)
        pace = _pace(dark, planned, [window])
    # Run 2: top-up and shortfalls with the counted PV, as a time margin.
    counted = _Model(now, series, reduced, running)
    planned = [0.0] * n
    top_phase = 0.0
    top_total = 0.0
    deadlines: list[int] = []
    if wanted is not None:
        _plan_top_up(counted, planned, wanted, end, latest=True)
        top_phase = planned[0]
        top_total = sum(planned)
        deadlines.append(window)
    run = _hold(counted, planned, counted.run(planned))
    if sum(run.shortfall) > _EPSILON:
        deadlines.extend(i for i, s in enumerate(run.shortfall) if s > _EPSILON)
        _cover(counted, planned, run, latest=True)
    from_both = planned[0]
    total_both = sum(planned)
    pace = max(pace, _pace(counted, planned, deadlines))
    biggest = max(from_top_up, from_both)
    needs_any = max(total_top_up, total_both) > _EPSILON
    if biggest <= _EPSILON and not (latched and needs_any and series[0].qualifies):
        return 0.0, False, None
    power = min(max(pace / (1.0 - share), MIN_BUFFER_POWER), battery.max_charge_power)
    wanted_kwh = min(model.max_grid(0), power * model.hours[0] / 1000.0)
    amount = _feasible(model, [0.0] * n, 0, wanted_kwh)
    if biggest > _EPSILON:
        by_top_up = from_top_up >= from_both - _EPSILON or top_phase > _EPSILON
    else:
        by_top_up = total_top_up >= total_both - _EPSILON or top_total > _EPSILON
    return amount, not by_top_up, power


def _pace(model: _Model, planned: Sequence[float], deadlines: Sequence[int]) -> float:
    """The power in W that covers what ``planned`` places before each deadline (a
    quarter index) if it were spread evenly over the qualifying hours from now to
    it; the highest over the deadlines."""
    series = model.quarters
    best = 0.0
    for k in deadlines:
        hours = sum(model.hours[j] for j in range(k) if series[j].qualifies)
        energy = sum(planned[:k])
        if energy > _EPSILON and hours > _EPSILON:
            best = max(best, energy / hours * 1000.0)
    return best


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
    # Without planned charging every shortfall was given up on, and a shortfall
    # may only be covered by quarters after the one given up on before it.
    b = model.b
    efficiency = False
    for k, s in enumerate(shortfalls):
        after = shortfalls[k - 1] if k else -1
        candidates = [j for j in range(after + 1, s) if series[j].qualifies]
        if not candidates:
            continue
        if b.ignore_efficiency or any(
            series[j].price <= model.efficiency * series[s].price + _EPSILON
            for j in candidates
        ):
            return PlanReason.AT_TARGET
        efficiency = True
    if efficiency:
        return PlanReason.BLOCKED_BY_EFFICIENCY
    return PlanReason.NO_QUALIFYING_SLOT


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
    window = sum(1 for q in model.quarters if _utc(q.start) < end)
    model.top = (content, window)
    try:
        _top_up_steps(model, planned, content, window, latest)
    finally:
        model.fix_top_up(planned)


def _top_up_steps(
    model: _Model,
    planned: list[float],
    content: float,
    window: int,
    latest: bool,
) -> None:
    series = model.quarters
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
        # What is bought at ``j`` is not to be used up before the shortfall by the
        # qualifying quarters between them whose price is lower: hold them with it.
        added = [
            k
            for k in range(j + 1, target)
            if series[k].qualifies
            and not model.held[k]
            and planned[k] <= 0.0
            and series[k].price < series[target].price - _EPSILON
        ]
        if model.b.discharge_blocks:
            for k in added:
                model.held[k] = True
        else:
            added = []
        room = model.max_grid(j) - planned[j]
        amount = _feasible(model, planned, j, min(wanted, room))
        if amount > _EPSILON:
            planned[j] += amount
            trial = model.run(planned)
            if sum(trial.shortfall) < before_total - _PROGRESS:
                return trial
            planned[j] -= amount
        for k in added:
            model.held[k] = False
    given_up.add(target)
    return None


def _feasible(model: _Model, planned: list[float], j: int, amount: float) -> float:
    """The most of ``amount`` that can be added at ``j`` without any planned
    quarter ending above the Charge Target.

    A kWh more at ``j`` raises the content of a later quarter by at most ``e``, so
    stepping down by the excess divided by ``e`` never passes the answer; it is
    found in a few steps, from above. Bisection is the fallback.
    """
    if amount <= _EPSILON:
        return 0.0
    for _ in range(_STEPS):
        excess = _excess(model, planned, j, amount)
        if excess <= 0.0:
            return amount
        amount -= excess / model.root
        if amount <= 0.0:
            return 0.0
    low, high = 0.0, amount
    for _ in range(40):
        middle = (low + high) / 2.0
        if _excess(model, planned, j, middle) <= 0.0:
            low = middle
        else:
            high = middle
    return low


def _excess(model: _Model, planned: list[float], j: int, amount: float) -> float:
    """How far above the Charge Target the highest planned quarter ends if
    ``amount`` is added at ``j``; not above 0 when none does."""
    planned[j] += amount
    try:
        run = model.run(planned)
    finally:
        planned[j] -= amount
    limit = model.target + _FIT
    return max(
        (
            run.before[k]
            + (planned[k] + (amount if k == j else 0.0)) * model.root
            - limit
            for k in range(len(planned))
            if planned[k] > 0.0 or k == j
        ),
        default=0.0,
    )
