"""Replay recorded quarter-hours of one House through the Charging Planner.

A development tool, not part of the integration. At every quarter-hour it builds
what the planner knew then (prices only as far as they were published), calls
``plan_charging`` the way ``grid_charging.py`` does, applies the plan to a
simulated battery with the real load and PV, and reports the energy bought and
its cost per local day and in total, against two baselines. See
``tools/README.md``.

It runs with plain Python: the planner is a pure module, loaded by file path so
that the integration's package (which imports Home Assistant) is not imported.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PLANNER_FILE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "nulleinspeisung"
    / "charging_planner.py"
)
SLOT = timedelta(minutes=15)
HOURS = 0.25
MODES = ("current", "none", "price-level-block")
MODE_LABELS = {
    "current": "current rules",
    "none": "baseline: no Grid Charging",
    "price-level-block": "baseline: price-level-block (before ticket 37)",
}
DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_PRICES_KNOWN_AT = time(13, 0)
_EPSILON = 1e-9


def load_planner() -> Any:
    """The planner module, loaded by file path (no Home Assistant needed)."""
    name = "nulleinspeisung_charging_planner"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLANNER_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PLANNER_FILE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


planner = load_planner()


@dataclass(frozen=True)
class House:
    """The House and the owner's settings."""

    capacity_kwh: float
    max_charge_power_w: float
    efficiency: float
    charge_target: float
    floor: float
    forecast_share: float
    start_charge_level: float
    top_up_level: float | None = None
    safety_buffer: float | None = None
    """Percent of the Maximum Charge Power, as in the settings."""


@dataclass(frozen=True)
class Record:
    """One recorded quarter-hour: the real values and what was forecast."""

    start: datetime
    price: float
    qualifies: bool
    load_w: float
    pv_w: float
    forecast_load_w: float
    forecast_pv_w: float
    support_w: float


@dataclass
class Totals:
    """What one day (or the whole period) came to."""

    bought_kwh: float = 0.0
    cost: float = 0.0
    charged_kwh: float = 0.0
    """Grid energy bought for charging the battery."""
    charged_cost: float = 0.0
    battery_kwh: float = 0.0
    """Energy delivered by the battery to the House."""
    nonqualifying_kwh: float = 0.0
    """Energy bought in quarter-hours that do not qualify for charging."""
    nonqualifying_cost: float = 0.0
    unstored_pv_kwh: float = 0.0
    """PV surplus the battery could not take (exported or curtailed)."""
    end_level: float = 0.0
    """Charge level in percent at the end."""

    def add(self, other: Totals) -> None:
        """Add the sums of ``other`` (the end level is set by the caller)."""
        for name in (
            "bought_kwh",
            "cost",
            "charged_kwh",
            "charged_cost",
            "battery_kwh",
            "nonqualifying_kwh",
            "nonqualifying_cost",
            "unstored_pv_kwh",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class Run:
    """The outcome of one replay mode."""

    mode: str
    days: dict[date, Totals] = field(default_factory=dict)
    total: Totals = field(default_factory=Totals)
    end_content_kwh: float = 0.0
    """Energy above the floor in the battery at the end."""
    stored_value: float = 0.0
    """Worth of what the battery still holds at the end (see ``stored_value``)."""

    @property
    def net_cost(self) -> float:
        """Cost less the stored value: comparable between different end levels."""
        return self.total.cost - self.stored_value


def parse_input(data: dict[str, Any]) -> tuple[House, list[Record]]:
    """Read the replay input format."""
    h = data["house"]
    house = House(
        capacity_kwh=float(h["capacity_kwh"]),
        max_charge_power_w=float(h["max_charge_power_w"]),
        efficiency=float(h["efficiency"]),
        charge_target=float(h["charge_target"]),
        floor=float(h["floor"]),
        forecast_share=float(h["forecast_share"]),
        start_charge_level=float(h["start_charge_level"]),
        top_up_level=None
        if h.get("top_up_level") is None
        else float(h["top_up_level"]),
        safety_buffer=None
        if h.get("safety_buffer") is None
        else float(h["safety_buffer"]),
    )
    records = []
    for q in data["quarters"]:
        start = datetime.fromisoformat(q["start"])
        if start.tzinfo is None:
            raise ValueError(f"quarter start {q['start']!r} has no time zone")
        load = float(q["load_w"])
        pv = float(q["pv_w"])
        records.append(
            Record(
                start=start,
                price=float(q["price"]),
                qualifies=bool(q["qualifies"]),
                load_w=load,
                pv_w=pv,
                forecast_load_w=float(q.get("forecast_load_w", load)),
                forecast_pv_w=float(q.get("forecast_pv_w", pv)),
                support_w=float(q.get("support_w", 0.0)),
            )
        )
    records.sort(key=lambda r: r.start)
    return house, records


def prices_known_until(
    now: datetime, tz: ZoneInfo, known_at: time = DEFAULT_PRICES_KNOWN_AT
) -> datetime:
    """The end of the published prices at ``now``: the end of the local day, or of
    the next one from ``known_at`` (13:00) on."""
    local = now.astimezone(tz)
    days = 2 if local.time() >= known_at else 1
    end = datetime.combine(local.date() + timedelta(days=days), time(0), tzinfo=tz)
    return end


def stored_value(content_kwh: float, efficiency: float, mean_price: float) -> float:
    """What ``content_kwh`` (above the floor) in the battery is worth: the energy
    it can still deliver, ``content`` times the square root of the efficiency,
    at the mean price of the period."""
    return content_kwh * math.sqrt(efficiency) * mean_price


class _Replay:
    """One replay of the records in one mode."""

    def __init__(
        self,
        house: House,
        records: list[Record],
        mode: str,
        tz: ZoneInfo,
        known_at: time,
    ):
        self.house, self.records, self.mode = house, records, mode
        self.tz, self.known_at = tz, known_at
        self.root = math.sqrt(min(max(house.efficiency, 0.01), 1.0))
        floor = min(max(house.floor, 0.0), 100.0)
        self.floor = floor
        self.full = house.capacity_kwh * (100.0 - floor) / 100.0
        self.target = min(
            max(house.capacity_kwh * (house.charge_target - floor) / 100.0, 0.0),
            self.full,
        )
        self.content = min(
            max(house.capacity_kwh * (house.start_charge_level - floor) / 100.0, 0.0),
            self.full,
        )
        self.plan: Any = None
        self.buffer_charged = False
        self.reached_day: date | None = None

    def level(self) -> float:
        """The charge level in percent."""
        return self.floor + self.content / self.house.capacity_kwh * 100.0

    def view(self, index: int) -> list[Any]:
        """The planner's quarters at ``index``: to the end of the known prices,
        with the forecast fields and the forecast share for PV."""
        now = self.records[index].start
        end = prices_known_until(now, self.tz, self.known_at)
        share = self.house.forecast_share / 100.0
        out = []
        for r in self.records[index:]:
            if r.start >= end:
                break
            out.append(
                planner.Quarter(
                    start=r.start,
                    price=r.price,
                    qualifies=r.qualifies,
                    load_w=r.forecast_load_w,
                    pv_w=r.forecast_pv_w * share,
                    support_w=r.support_w,
                )
            )
        return out

    def top_up(self, now: datetime, view: list[Any]) -> Any:
        """The top-up to plan, if one is configured and not reached today.

        The window ends at local midnight, or at the end of the run of qualifying
        quarter-hours that goes on at midnight (as far as the prices are known)."""
        level = self.house.top_up_level
        if level is None:
            return None
        local_day = now.astimezone(self.tz).date()
        if self.reached_day == local_day:
            return None
        midnight = datetime.combine(
            local_day + timedelta(days=1), time(0), tzinfo=self.tz
        )
        qualifying = {q.start: q.qualifies for q in view}
        end = midnight
        if qualifying.get(midnight - SLOT, False):
            while qualifying.get(end, False):
                end += SLOT
        return planner.TopUp(level=level, end=end)

    def make_plan(self, index: int) -> Any:
        """Call the planner like ``GridCharging._make_plan``."""
        now = self.records[index].start
        view = self.view(index)
        h = self.house
        running = None
        if self.plan is not None:
            running = next((s.start for s in self.plan.slots if s.start == now), None)
        return planner.plan_charging(
            now,
            view,
            planner.BatteryModel(
                capacity_kwh=h.capacity_kwh,
                charge_level=self.level(),
                floor=self.floor,
                charge_target=h.charge_target,
                max_charge_power=h.max_charge_power_w,
                efficiency=h.efficiency,
                discharge_blocks=True,
            ),
            running=running,
            top_up=self.top_up(now, view),
            buffer=None if h.safety_buffer is None else h.safety_buffer / 100.0,
            buffer_latched=self.buffer_charged,
        )

    def run(self) -> Run:
        """Replay every record."""
        result = Run(self.mode)
        for index, r in enumerate(self.records):
            held, charge_kwh = False, 0.0
            if self.mode == "current":
                local_day = r.start.astimezone(self.tz).date()
                if self.level() >= (self.house.top_up_level or 101.0) - 1e-6:
                    self.reached_day = local_day
                plan = self.plan = self.make_plan(index)
                slot = next((s for s in plan.slots if s.start == r.start), None)
                charge_kwh = slot.grid_kwh if slot is not None else 0.0
                held = plan.is_held(r.start)
                # The latch: the buffer goes on if it charged in this quarter-hour.
                self.buffer_charged = charge_kwh > 0.0 and plan.buffer_kwh > 0.0
            elif self.mode == "price-level-block":
                held = r.qualifies
            day = result.days.setdefault(r.start.astimezone(self.tz).date(), Totals())
            self.step(r, held, charge_kwh, day)
            day.end_level = self.level()
        for day in result.days.values():
            result.total.add(day)
        result.total.end_level = self.level()
        result.end_content_kwh = self.content
        prices = [r.price for r in self.records]
        mean = sum(prices) / len(prices) if prices else 0.0
        result.stored_value = stored_value(self.content, self.house.efficiency, mean)
        return result

    def step(self, r: Record, held: bool, charge_kwh: float, day: Totals) -> None:
        """Apply one quarter-hour to the battery with the real load and PV."""
        root = self.root
        net = (r.load_w - r.pv_w - r.support_w) * HOURS / 1000.0  # kWh, + = deficit
        import_kwh = 0.0
        if charge_kwh > 0.0:
            # Charging from the grid, up to the Charge Target; the House's net load
            # comes from the grid as well, a PV surplus is not stored.
            room = max(self.target - self.content, 0.0)
            grid = min(charge_kwh, room / root)
            self.content += grid * root
            day.charged_kwh += grid
            day.charged_cost += grid * r.price
            day.bought_kwh += grid
            day.cost += grid * r.price
            if grid > 0.0 and not r.qualifies:
                day.nonqualifying_kwh += grid
                day.nonqualifying_cost += grid * r.price
        if net > 0.0:
            import_kwh = net
            if charge_kwh <= 0.0 and not held:
                delivered = min(net, self.content * root)
                self.content -= delivered / root
                day.battery_kwh += delivered
                import_kwh = net - delivered
        elif net < 0.0:
            surplus = -net
            if charge_kwh > 0.0:
                day.unstored_pv_kwh += surplus
            else:
                stored = min(surplus * root, self.full - self.content)
                self.content += stored
                day.unstored_pv_kwh += max(surplus - stored / root, 0.0)
        if import_kwh > _EPSILON:
            day.bought_kwh += import_kwh
            day.cost += import_kwh * r.price
            if not r.qualifies:
                day.nonqualifying_kwh += import_kwh
                day.nonqualifying_cost += import_kwh * r.price


def replay(
    house: House,
    records: list[Record],
    modes: tuple[str, ...] = MODES,
    timezone: str = DEFAULT_TIMEZONE,
    prices_known_at: time = DEFAULT_PRICES_KNOWN_AT,
) -> dict[str, Run]:
    """Replay the records in each mode."""
    tz = ZoneInfo(timezone)
    return {
        mode: _Replay(house, records, mode, tz, prices_known_at).run() for mode in modes
    }


def to_dict(runs: dict[str, Run]) -> dict[str, Any]:
    """The runs as plain data (the ``--json`` output)."""
    return {
        mode: {
            "label": MODE_LABELS[mode],
            "days": {d.isoformat(): asdict(t) for d, t in run.days.items()},
            "total": asdict(run.total),
            "end_content_kwh": run.end_content_kwh,
            "stored_value": run.stored_value,
            "net_cost": run.net_cost,
        }
        for mode, run in runs.items()
    }


_COLUMNS = (
    ("bought kWh", "bought_kwh", "{:8.2f}"),
    ("cost", "cost", "{:8.2f}"),
    ("charged kWh", "charged_kwh", "{:8.2f}"),
    ("charge cost", "charged_cost", "{:8.2f}"),
    ("battery kWh", "battery_kwh", "{:8.2f}"),
    ("dear kWh", "nonqualifying_kwh", "{:8.2f}"),
    ("dear cost", "nonqualifying_cost", "{:8.2f}"),
    ("PV unstored", "unstored_pv_kwh", "{:8.2f}"),
    ("end %", "end_level", "{:6.1f}"),
)


def format_table(runs: dict[str, Run]) -> str:
    """The runs as text tables, one per mode."""
    lines = []
    for run in runs.values():
        lines.append(f"== {MODE_LABELS[run.mode]}")
        lines.append(
            f"{'day':<11}" + "".join(f"{title:>13}" for title, _, _ in _COLUMNS)
        )
        rows = [(d.isoformat(), t) for d, t in run.days.items()]
        rows.append(("total", run.total))
        for label, totals in rows:
            lines.append(
                f"{label:<11}"
                + "".join(
                    f"{fmt.format(getattr(totals, name)):>13}"
                    for _, name, fmt in _COLUMNS
                )
            )
        lines.append(
            f"stored value {run.stored_value:.2f} "
            f"({run.end_content_kwh:.2f} kWh above the floor); "
            f"cost less stored value {run.net_cost:.2f}"
        )
        lines.append("")
    lines.append(
        "dear = bought in quarter-hours that do not qualify for charging; "
        "cost has no export revenue."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("input", type=Path, help="replay input JSON")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--timezone", default=DEFAULT_TIMEZONE, help="local time zone (days, prices)"
    )
    parser.add_argument(
        "--prices-known-at",
        default=DEFAULT_PRICES_KNOWN_AT.strftime("%H:%M"),
        help="local time from which the next day's prices are known (HH:MM)",
    )
    parser.add_argument(
        "--modes", nargs="+", choices=MODES, default=list(MODES), help="runs to make"
    )
    args = parser.parse_args(argv)
    house, records = parse_input(json.loads(args.input.read_text()))
    runs = replay(
        house,
        records,
        tuple(args.modes),
        args.timezone,
        time.fromisoformat(args.prices_known_at),
    )
    if args.json:
        print(json.dumps(to_dict(runs), indent=2))
    else:
        print(format_table(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
