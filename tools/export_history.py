"""Export recorded quarter-hours from Home Assistant's history for ``replay.py``.

A development tool, not part of the integration. It reads the REST history API
(``/api/history/period``) for the given entities, averages them to quarter-hours,
computes the House's load as grid power plus production minus battery power, and
writes the replay input format. The base URL and the token come from the
environment (``HA_URL``, ``HA_TOKEN``); they are never printed or written.

Exports describe a real installation: keep them in ``llm-wiki/private/``, which
git ignores. See ``tools/README.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SLOT = timedelta(minutes=15)

Point = tuple[datetime, float | str | None]
"""A state from ``start`` on: a number, a text (price level) or ``None`` (unknown)."""
Getter = Callable[[str, Mapping[str, str]], Any]
"""``(url, headers)`` to the decoded JSON of the response."""


def parse_state(raw: Any, numeric: bool) -> float | str | None:
    """A history state as a number (or text for a level); unknown ones are ``None``."""
    if raw is None or str(raw).lower() in ("unknown", "unavailable", ""):
        return None
    if not numeric:
        return str(raw)
    try:
        return float(raw)
    except ValueError:
        return None


def parse_history(
    rows: Sequence[Mapping[str, Any]], numeric: bool = True
) -> list[Point]:
    """The points of one entity from a ``minimal_response`` history list."""
    points: list[Point] = []
    for row in rows:
        stamp = row.get("last_changed") or row.get("last_updated")
        if stamp is None:
            continue
        points.append(
            (
                datetime.fromisoformat(stamp).astimezone(UTC),
                parse_state(row["state"], numeric),
            )
        )
    points.sort(key=lambda p: p[0])
    return points


def quarter_starts(start: datetime, end: datetime) -> list[datetime]:
    """The quarter-hour starts from ``start`` (rounded up) to before ``end``."""
    first = start.astimezone(UTC).replace(second=0, microsecond=0)
    first += timedelta(minutes=(-first.minute) % 15)
    out = []
    while first + SLOT <= end:
        out.append(first)
        first += SLOT
    return out


def average(points: Sequence[Point], quarter: datetime) -> float | None:
    """The time-weighted mean of a numeric series over the quarter-hour at
    ``quarter``: each state holds until the next one. Time without a known state
    counts for nothing; ``None`` when no time in the quarter-hour is known."""
    end = quarter + SLOT
    total = weight = 0.0
    for i, (at, value) in enumerate(points):
        until = points[i + 1][0] if i + 1 < len(points) else end
        lo, hi = max(at, quarter), min(until, end)
        seconds = (hi - lo).total_seconds()
        if seconds > 0 and isinstance(value, float):
            total += value * seconds
            weight += seconds
    return total / weight if weight else None


def value_at(points: Sequence[Point], moment: datetime) -> float | str | None:
    """The state valid at ``moment`` (the last one that began at or before it)."""
    current = None
    for at, value in points:
        if at > moment:
            break
        current = value
    return current


def build_input(
    quarters: Sequence[datetime],
    grid: Sequence[Point],
    production: Sequence[Sequence[Point]],
    battery: Sequence[Point] | None,
    price: Sequence[Point],
    level: Sequence[Point],
    qualifying_levels: Sequence[str],
    house: Mapping[str, Any],
) -> dict[str, Any]:
    """The replay input: per quarter-hour the mean prices and powers.

    ``load = grid + production - battery`` with grid power positive for import and
    battery power positive while charging. Quarter-hours with no price, no level
    or no grid value are left out; missing production or battery counts as 0."""
    wanted = {name.lower() for name in qualifying_levels}
    out = []
    for q in quarters:
        grid_w = average(grid, q)
        euro = average(price, q)
        lvl = value_at(level, q)
        if grid_w is None or euro is None or lvl is None:
            continue
        pv_w = sum(average(series, q) or 0.0 for series in production)
        battery_w = (average(battery, q) or 0.0) if battery is not None else 0.0
        out.append(
            {
                "start": q.isoformat(),
                "price": round(euro, 5),
                "qualifies": str(lvl).lower() in wanted,
                "load_w": round(grid_w + pv_w - battery_w, 1),
                "pv_w": round(pv_w, 1),
            }
        )
    return {"house": dict(house), "quarters": out}


def fetch_history(
    base_url: str,
    token: str,
    entity_ids: Sequence[str],
    start: datetime,
    end: datetime,
    get: Getter | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The history of each entity by the REST API, ``minimal_response``."""
    query = urllib.parse.urlencode(
        {
            "filter_entity_id": ",".join(entity_ids),
            "end_time": end.isoformat(),
            "minimal_response": "1",
            "no_attributes": "1",
        }
    )
    url = (
        f"{base_url.rstrip('/')}/api/history/period/"
        f"{urllib.parse.quote(start.isoformat())}?{query}"
    )
    data = (get or _http_get)(url, {"Authorization": f"Bearer {token}"})
    result: dict[str, list[dict[str, Any]]] = {e: [] for e in entity_ids}
    for rows in data:
        if rows:
            entity = rows[0].get("entity_id")
            if entity in result:
                result[entity] = list(rows)
    return result


def _http_get(url: str, headers: Mapping[str, str]) -> Any:
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def export(
    args: argparse.Namespace,
    base_url: str,
    token: str,
    get: Getter | None = None,
) -> dict[str, Any]:
    """Fetch, average and build the replay input for the parsed ``args``."""
    start, end = args.start, args.end
    ids = [args.grid, *args.production, args.price, args.level]
    if args.battery:
        ids.append(args.battery)
    history = fetch_history(base_url, token, ids, start, end, get)

    def series(entity: str, numeric: bool = True) -> list[Point]:
        return parse_history(history[entity], numeric)

    house = {
        "capacity_kwh": args.capacity_kwh,
        "max_charge_power_w": args.max_charge_power_w,
        "efficiency": args.efficiency,
        "charge_target": args.charge_target,
        "floor": args.floor,
        "top_up_level": args.top_up_level,
        "safety_buffer": args.safety_buffer,
        "forecast_share": args.forecast_share,
        "start_charge_level": args.start_charge_level,
    }
    return build_input(
        quarter_starts(start, end),
        series(args.grid),
        [series(entity) for entity in args.production],
        series(args.battery) if args.battery else None,
        series(args.price),
        series(args.level, numeric=False),
        args.qualifying_levels,
        house,
    )


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} needs a time zone offset")
    return moment


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "output", type=Path, help="file to write (keep it in llm-wiki/private/)"
    )
    p.add_argument("--start", type=_moment, required=True, help="ISO time with offset")
    p.add_argument("--end", type=_moment, required=True, help="ISO time with offset")
    p.add_argument(
        "--grid", required=True, help="entity: grid power in W, import positive"
    )
    p.add_argument(
        "--production",
        nargs="+",
        required=True,
        help="entities: inverter production in W (summed)",
    )
    p.add_argument(
        "--battery", help="entity: battery power in W, positive while charging"
    )
    p.add_argument("--price", required=True, help="entity: price per kWh")
    p.add_argument("--level", required=True, help="entity: Price Level")
    p.add_argument(
        "--qualifying-levels",
        nargs="+",
        default=["very_cheap", "cheap"],
        help="Price Level states that qualify for charging",
    )
    p.add_argument("--capacity-kwh", type=float, required=True)
    p.add_argument("--max-charge-power-w", type=float, required=True)
    p.add_argument("--efficiency", type=float, default=0.8, help="round trip, fraction")
    p.add_argument("--charge-target", type=float, default=100.0)
    p.add_argument("--floor", type=float, default=10.0)
    p.add_argument("--top-up-level", type=float, default=None)
    p.add_argument("--safety-buffer", type=float, default=None)
    p.add_argument("--forecast-share", type=float, default=100.0)
    p.add_argument("--start-charge-level", type=float, default=50.0)
    return p


def main(argv: list[str] | None = None) -> int:
    """Export to the file given; the token is read from the environment only."""
    args = build_parser().parse_args(argv)
    base_url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
    if not base_url or not token:
        print("set HA_URL and HA_TOKEN in the environment", file=sys.stderr)
        return 2
    data = export(args, base_url, token)
    args.output.write_text(json.dumps(data, indent=1))
    print(f"wrote {len(data['quarters'])} quarter-hours to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
