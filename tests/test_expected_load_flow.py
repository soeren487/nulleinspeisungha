"""The Expected Load of a House, end to end with simulated time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import (
    DtuNetwork,
    SimDtu,
    SimHouse,
    seed_load_history,
    setup_entry,
)

BERLIN = ZoneInfo("Europe/Berlin")
START = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # 10:00 in Berlin
STORAGE_KEY = f"{DOMAIN}.load_history_house-home"
METER = "sensor.grid_meter"
TODAY = START.astimezone(BERLIN).date()


async def setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    hass_storage: dict[str, Any],
    history: list | None = None,
    grid: str = "300",
    at: datetime = START,
):
    """Set up the House 'Home' without Inverters, drawing ``grid`` W from the grid."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(at)
    hass.states.async_set(METER, grid, {"unit_of_measurement": "W"})
    if history is not None:
        seed_load_history(hass_storage, "house-home", history)
    entry = await setup_entry(
        hass, dtu_network, SimDtu.default(), houses=[SimHouse("Home")]
    )
    return entry, next(iter(entry.runtime_data.houses.values()))


async def run(hass: HomeAssistant, freezer, seconds: int, step: int = 30) -> None:
    """Let time pass ``step`` seconds at a time."""
    for _ in range(seconds // step):
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def stored(hass_storage: dict[str, Any]) -> list[list]:
    return hass_storage[STORAGE_KEY]["data"]["records"]


def week(days: int = 8, watts=lambda slot: 100.0 + 10 * (slot // 4)) -> list:
    """Full days of consumption before today: 100 W at midnight, 10 W more per hour."""
    records = []
    for i in range(1, days + 1):
        midnight = datetime(*(TODAY - timedelta(days=i)).timetuple()[:3], tzinfo=BERLIN)
        for slot in range(96):
            records.append((midnight + timedelta(minutes=15 * slot), watts(slot)))
    return [(s.astimezone(UTC), w) for s, w in records]


def entity(hass: HomeAssistant, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"house-home_{key}"
    )
    assert entity_id is not None, key
    return entity_id


def state(hass: HomeAssistant, domain: str, key: str) -> str:
    found = hass.states.get(entity(hass, domain, key))
    assert found is not None
    return found.state


# -- recording -----------------------------------------------------------------


async def test_a_quarter_hour_of_steady_consumption_is_recorded(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """300 W from 10:00 to 10:15: one record of 300 W, written to storage."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage)
    assert house.expected_load.history == ()
    await run(hass, freezer, 15 * 60)
    [record] = house.expected_load.history
    assert record.start == START
    assert record.watts == pytest.approx(300.0)
    assert stored(hass_storage) == [[int(START.timestamp()), pytest.approx(300.0)]]


async def test_the_record_holds_the_mean_of_changing_consumption(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Half the quarter-hour at 300 W, half at 500 W: about 400 W."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage)
    await run(hass, freezer, 450)
    hass.states.async_set(METER, "500", {"unit_of_measurement": "W"})
    await run(hass, freezer, 450)
    [record] = house.expected_load.history
    assert 390.0 < record.watts < 410.0


async def test_a_quarter_hour_without_the_grid_meter_is_skipped(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Ten of fifteen minutes without a reading leave less than half: no record."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage)
    await run(hass, freezer, 150)
    hass.states.async_set(METER, "unavailable")
    await run(hass, freezer, 10 * 60)
    hass.states.async_set(METER, "300", {"unit_of_measurement": "W"})
    await run(hass, freezer, 150)
    assert house.expected_load.history == ()
    # The next quarter-hour is complete again and is recorded.
    await run(hass, freezer, 15 * 60)
    assert [r.start for r in house.expected_load.history] == [
        START + timedelta(minutes=15)
    ]


async def test_a_short_gap_still_leaves_a_record_of_the_valid_samples(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Two minutes without a reading do not matter: the mean is of what was read."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage)
    await run(hass, freezer, 5 * 60)
    hass.states.async_set(METER, "unavailable")
    await run(hass, freezer, 2 * 60)
    hass.states.async_set(METER, "300", {"unit_of_measurement": "W"})
    await run(hass, freezer, 8 * 60)
    [record] = house.expected_load.history
    assert record.watts == pytest.approx(300.0)


async def test_a_negative_mean_is_stored_as_zero(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """The Grid Meter reading -200 W (export) is no consumption: 0 W."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage, grid="-200")
    await run(hass, freezer, 15 * 60)
    [record] = house.expected_load.history
    assert record.watts == 0.0
    assert stored(hass_storage) == [[int(START.timestamp()), 0.0]]


async def test_the_history_survives_a_reload_of_the_entry(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """After reloading, the recorded quarter-hours are still there."""
    entry, house = await setup(hass, dtu_network, freezer, hass_storage)
    await run(hass, freezer, 30 * 60)
    history = house.expected_load.history
    assert len(history) == 2
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = next(iter(entry.runtime_data.houses.values())).expected_load
    assert reloaded.history == history


async def test_the_history_is_written_once_per_quarter_hour(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """The record finished at 10:15 is on disk then; the next one at 10:30."""
    await setup(hass, dtu_network, freezer, hass_storage)
    await run(hass, freezer, 15 * 60)
    assert len(stored(hass_storage)) == 1
    await run(hass, freezer, 14 * 60)
    assert len(stored(hass_storage)) == 1
    await run(hass, freezer, 60)
    assert len(stored(hass_storage)) == 2


async def test_records_older_than_35_days_are_dropped(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """A record from 36 days ago goes, one from 34 days ago stays."""
    old = START - timedelta(days=36)
    recent = START - timedelta(days=34)
    entry, house = await setup(
        hass,
        dtu_network,
        freezer,
        hass_storage,
        history=[(old, 100.0), (recent, 200.0)],
    )
    assert [r.start for r in house.expected_load.history] == [recent]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert stored(hass_storage) == [[int(recent.timestamp()), 200.0]]


# -- entities --------------------------------------------------------------------


async def test_sensors_show_the_learned_values_for_a_frozen_instant(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """At 10:07 in Berlin with eight days of history: 200 W now, 5.16 kWh a day."""
    await setup(hass, dtu_network, freezer, hass_storage, history=week(8))
    assert float(state(hass, "sensor", "expected_load")) == pytest.approx(200.0)
    assert float(state(hass, "sensor", "expected_load_24h")) == pytest.approx(5.16)
    assert state(hass, "sensor", "load_history_days") == "8"
    assert state(hass, "binary_sensor", "expected_load_learned") == "on"
    # The fallback does not matter once learned.
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": entity(hass, "number", "fallback_daily_consumption"),
            "value": 24,
        },
        blocking=True,
    )
    assert float(state(hass, "sensor", "expected_load")) == pytest.approx(200.0)


async def test_with_less_than_a_week_the_fallback_is_shown(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Three days of history: the default 10 kWh a day, 416.7 W, and not learned."""
    await setup(hass, dtu_network, freezer, hass_storage, history=week(3))
    assert float(state(hass, "sensor", "expected_load")) == pytest.approx(
        416.7, abs=0.05
    )
    assert float(state(hass, "sensor", "expected_load_24h")) == pytest.approx(10.0)
    assert state(hass, "sensor", "load_history_days") == "3"
    assert state(hass, "binary_sensor", "expected_load_learned") == "off"


async def test_changing_the_fallback_changes_the_values(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """12 kWh a day is 500 W and 12 kWh; the owner sets 24 and sees 1000 W, 24 kWh."""
    await setup(hass, dtu_network, freezer, hass_storage)
    number = entity(hass, "number", "fallback_daily_consumption")
    assert float(state(hass, "number", "fallback_daily_consumption")) == 10.0
    for kwh, watts in ((12, 500.0), (24, 1000.0)):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": kwh}, blocking=True
        )
        assert float(state(hass, "sensor", "expected_load")) == pytest.approx(watts)
        assert float(state(hass, "sensor", "expected_load_24h")) == pytest.approx(kwh)


async def test_the_fallback_survives_a_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """The owner's 24 kWh are still there after the entry is reloaded."""
    entry, _ = await setup(hass, dtu_network, freezer, hass_storage)
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": entity(hass, "number", "fallback_daily_consumption"),
            "value": 24,
        },
        blocking=True,
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(state(hass, "number", "fallback_daily_consumption")) == 24.0
    assert float(state(hass, "sensor", "expected_load")) == pytest.approx(1000.0)


async def test_the_house_offers_the_next_48_hours(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """192 quarter-hours from the current one, and the Expected Load at any instant."""
    _, house = await setup(hass, dtu_network, freezer, hass_storage, history=week(8))
    upcoming = house.expected_load.upcoming()
    assert len(upcoming) == 192
    assert upcoming[0][0] == START
    assert upcoming[0][1] == pytest.approx(200.0)
    assert upcoming[-1][0] == START + timedelta(hours=47, minutes=45)
    assert house.expected_load.at(START + timedelta(hours=14)) == pytest.approx(
        100.0 + 10 * 0
    )
