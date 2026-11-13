"""The replay and export tools (``tools/``), on artificial data: no network."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools import export_history, replay

FIXTURES = Path(__file__).parent / "fixtures" / "replay"


def run_fixture(name: str) -> dict[str, replay.Run]:
    house, records = replay.parse_input(json.loads((FIXTURES / name).read_text()))
    return replay.replay(house, records)


def test_three_days_cheap_night_dear_evening() -> None:
    """Three artificial days from 2026-11-02, a 10 kWh battery with a floor of 10 %
    that starts at the floor, 2 kW charging, efficiency 0.81 (``e`` = 0.9).

    Each day: 00-06 cheap (0.10, qualifies), 06-17 and 21-24 normal (0.30), 17-21
    dear (0.50), load 400 W except 1500 W in the evening, PV a half sine from 08
    to 16 up to 2.5 kW (12.7 kWh on a sunny day, 30 % of that on the cloudy second
    day). Prices are published for the next day from 13:00, so on day 1 the
    planner sees the evening before it sees day 2.

    The evening load is 4.4 kWh at the meter. The battery charges at night
    (11.9 kWh from the grid over the three days, 0.10 per kWh) and covers the
    evening; the cloudy day needs most of it (9.61 kWh charged on the night of
    the 3rd). Without Grid Charging the evenings are bought at 0.50."""
    runs = run_fixture("three_days.json")
    current, none, block = runs["current"], runs["none"], runs["price-level-block"]
    assert current.total.bought_kwh == pytest.approx(19.13, abs=0.01)
    assert current.total.cost == pytest.approx(1.91, abs=0.01)
    assert current.total.charged_kwh == pytest.approx(11.93, abs=0.01)
    assert current.total.charged_cost == pytest.approx(1.19, abs=0.01)
    assert current.total.battery_kwh == pytest.approx(26.12, abs=0.01)
    assert current.total.nonqualifying_kwh == pytest.approx(0.0, abs=0.01)
    assert none.total.cost == pytest.approx(4.80, abs=0.01)
    assert none.total.nonqualifying_kwh == pytest.approx(9.88, abs=0.01)
    assert block.total.cost == pytest.approx(4.75, abs=0.01)
    # Grid Charging pays: dear evenings are covered by cheap night energy.
    assert current.total.cost < none.total.cost - 2.0
    assert current.net_cost < none.net_cost
    assert list(current.days) == sorted(current.days)
    assert len(current.days) == 3


def test_cheap_39_hours_costs_less_than_price_level_block() -> None:
    """The situation of 2026-11-11 to 13: a long run of qualifying (cheap) quarters
    (39 h, from 2026-11-10 15:00 local), 20 kWh battery at 95 %, constant 350 W,
    then 21 h at 0.40 (not qualifying). The dear prices are published on the 11th
    at 13:00.

    The old rule holds the battery in every cheap quarter: it buys the whole load
    at 0.20 (13.65 kWh, 2.73) and keeps the battery at 54 %. The current rule only
    holds what the dear hours need: 5.70 kWh bought, 1.33. (The battery was
    emptied for the 22 h before the dear prices were known, so 0.97 kWh is still
    bought dear; the old rule keeps more energy: its stored value is higher, see
    ``net_cost``.)"""
    runs = run_fixture("cheap_39_hours.json")
    current, none, block = runs["current"], runs["none"], runs["price-level-block"]
    assert block.total.bought_kwh == pytest.approx(13.65, abs=0.01)
    assert block.total.cost == pytest.approx(2.73, abs=0.01)
    assert block.total.end_level == pytest.approx(54.17, abs=0.01)
    assert current.total.bought_kwh == pytest.approx(5.70, abs=0.01)
    assert current.total.cost == pytest.approx(1.33, abs=0.01)
    assert current.total.nonqualifying_kwh == pytest.approx(0.97, abs=0.01)
    assert none.total.cost == pytest.approx(2.28, abs=0.01)
    assert current.total.cost < block.total.cost
    assert current.total.cost < none.total.cost


def test_energy_balance() -> None:
    """Without PV the House buys what the battery does not deliver."""
    house, records = replay.parse_input(
        json.loads((FIXTURES / "cheap_39_hours.json").read_text())
    )
    load_kwh = sum(r.load_w for r in records) * 0.25 / 1000.0
    for run in replay.replay(house, records).values():
        t = run.total
        assert t.bought_kwh - t.charged_kwh + t.battery_kwh == pytest.approx(
            load_kwh, abs=1e-6
        )


def test_prices_known_from_13_local() -> None:
    tz = replay.ZoneInfo("Europe/Berlin")
    before = datetime(2026, 11, 10, 11, 45, tzinfo=UTC)  # 12:45 local
    after = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # 13:00 local
    assert replay.prices_known_until(before, tz) == datetime(2026, 11, 11, 0, tzinfo=tz)
    assert replay.prices_known_until(after, tz) == datetime(2026, 11, 12, 0, tzinfo=tz)


def test_cli_table_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(FIXTURES / "three_days.json")
    assert replay.main([path]) == 0
    out = capsys.readouterr().out
    assert "current rules" in out
    assert "price-level-block" in out
    assert "total" in out
    assert replay.main([path, "--json", "--modes", "current", "none"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"current", "none"}
    assert data["current"]["total"]["cost"] == pytest.approx(1.91, abs=0.01)
    assert len(data["none"]["days"]) == 3


# -- export ------------------------------------------------------------------

T0 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def point(minutes: float, value: float | str | None) -> export_history.Point:
    return T0 + timedelta(minutes=minutes), value


def test_average_is_time_weighted() -> None:
    """100 W for 5 min, 400 W for 10 min: (500 + 4000) / 15 = 300 W."""
    series = [point(-60, 999.0), point(0, 100.0), point(5, 400.0)]
    assert export_history.average(series, T0) == pytest.approx(300.0)


def test_average_ignores_unknown_time() -> None:
    series = [point(0, None), point(5, 200.0)]  # unknown for 5 min, 200 W for 10
    assert export_history.average(series, T0) == pytest.approx(200.0)
    assert export_history.average([point(0, None)], T0) is None
    assert export_history.average([], T0) is None


def test_value_at_and_parse_history() -> None:
    rows = [
        {"entity_id": "sensor.x", "state": "cheap", "last_changed": T0.isoformat()},
        {"state": "unavailable", "last_changed": (T0 + timedelta(hours=1)).isoformat()},
    ]
    levels = export_history.parse_history(rows, numeric=False)
    assert export_history.value_at(levels, T0 + timedelta(minutes=30)) == "cheap"
    numbers = export_history.parse_history(
        [{"state": "12.5", "last_changed": T0.isoformat()}]
    )
    assert numbers == [(T0, 12.5)]


def test_build_input_computes_load() -> None:
    """Load = grid + production - battery: 300 + 700 - 500 (charging) = 500 W."""
    grid = [point(0, 300.0)]
    production = [[point(0, 400.0)], [point(0, 300.0)]]
    battery = [point(0, 500.0)]
    price = [point(0, 0.2)]
    level = [point(0, "very_cheap")]
    data = export_history.build_input(
        export_history.quarter_starts(T0, T0 + timedelta(minutes=30)),
        grid,
        production,
        battery,
        price,
        level,
        ["very_cheap", "cheap"],
        {"capacity_kwh": 10.0},
    )
    assert data["house"] == {"capacity_kwh": 10.0}
    assert len(data["quarters"]) == 2
    first = data["quarters"][0]
    assert first == {
        "start": T0.isoformat(),
        "price": 0.2,
        "qualifies": True,
        "load_w": 500.0,
        "pv_w": 700.0,
    }


def test_build_input_skips_quarters_without_data() -> None:
    data = export_history.build_input(
        export_history.quarter_starts(T0, T0 + timedelta(minutes=30)),
        [point(15, 100.0)],
        [],
        None,
        [point(0, 0.3)],
        [point(0, "normal")],
        ["cheap"],
        {},
    )
    assert [q["start"] for q in data["quarters"]] == [
        (T0 + timedelta(minutes=15)).isoformat()
    ]
    assert data["quarters"][0]["qualifies"] is False
    assert data["quarters"][0]["load_w"] == 100.0


def test_fetch_history_and_export_with_fake_api(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_get(url: str, headers: dict[str, str]) -> list[list[dict[str, str]]]:
        calls.append((url, dict(headers)))
        start = T0.isoformat()

        def rows(entity: str, state: str) -> list[dict[str, str]]:
            return [{"entity_id": entity, "state": state, "last_changed": start}]

        return [
            rows("sensor.grid", "200"),
            rows("sensor.pv", "100"),
            rows("sensor.price", "0.25"),
            rows("sensor.level", "cheap"),
        ]

    parser = export_history.build_parser()
    args = parser.parse_args(
        [
            str(tmp_path / "out.json"),
            "--start",
            T0.isoformat(),
            "--end",
            (T0 + timedelta(minutes=30)).isoformat(),
            "--grid",
            "sensor.grid",
            "--production",
            "sensor.pv",
            "--price",
            "sensor.price",
            "--level",
            "sensor.level",
            "--qualifying-levels",
            "cheap",
            "--capacity-kwh",
            "10",
            "--max-charge-power-w",
            "2000",
        ]
    )
    assert isinstance(args, argparse.Namespace)
    data = export_history.export(args, "http://ha.invalid/", "secret", fake_get)
    assert [q["load_w"] for q in data["quarters"]] == [300.0, 300.0]
    assert data["quarters"][0]["qualifies"] is True
    url, headers = calls[0]
    assert url.startswith("http://ha.invalid/api/history/period/")
    assert "minimal_response" in url
    assert headers == {"Authorization": "Bearer secret"}
    assert "secret" not in json.dumps(data)
    # The export reads back into the replay.
    house, records = replay.parse_input(data)
    assert len(records) == 2
    assert house.capacity_kwh == 10.0


def test_export_needs_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    argv = [
        "out.json",
        "--start", T0.isoformat(), "--end", T0.isoformat(),
        "--grid", "a", "--production", "b", "--price", "c", "--level", "d",
        "--capacity-kwh", "1", "--max-charge-power-w", "1",
    ]  # fmt: skip
    assert export_history.main(argv) == 2
    assert "HA_TOKEN" in capsys.readouterr().err
