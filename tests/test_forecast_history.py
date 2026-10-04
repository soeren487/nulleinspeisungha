"""The production history of a House, end to end with simulated DTUs and time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import (
    DtuNetwork,
    SimDtu,
    SimHouse,
    SimOpenMeteo,
    seed_history,
    setup_entry,
)
from tests.test_forecast_flow import (
    day_records,
    dtu,
    prepared_history,
)

OMA, BUERO4 = "114183178036", "116191100801"
START = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
STORAGE_KEY = f"{DOMAIN}.pv_history_house-home"
METER = "sensor.grid_meter"


async def setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    freezer,
    hass_storage: dict[str, Any],
    history: list | None = None,
    sim: SimDtu | None = None,
    at: datetime = START,
):
    """Set up the House 'Home' (500 W of PV production) at ``at``."""
    freezer.move_to(at)
    if history is not None:
        seed_history(hass_storage, "house-home", history)
    entry = await setup_entry(
        hass,
        dtu_network,
        sim or dtu(),
        houses=[SimHouse("Home", inverters=[OMA, BUERO4])],
        open_meteo=open_meteo,
    )
    return entry, next(iter(entry.runtime_data.houses.values()))


async def run(hass: HomeAssistant, freezer, seconds: int, step: int = 10) -> None:
    """Let time pass ``step`` seconds at a time, as the DTUs are polled."""
    for _ in range(seconds // step):
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def stored_records(hass_storage: dict[str, Any]) -> list[list]:
    return hass_storage[STORAGE_KEY]["data"]["records"]


async def test_a_quarter_hour_of_steady_production_is_recorded(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """14:00 to 14:15 at 500 W: one record with the mean and the forecast irradiance."""
    _, house = await setup(hass, dtu_network, open_meteo, freezer, hass_storage)
    assert house.forecast.history == ()
    await run(hass, freezer, 15 * 60)
    [record] = house.forecast.history
    assert record.start == START
    assert abs(record.production - 500.0) < 0.001
    assert record.irradiance == 282.0
    assert record.curtailed is False
    # Written to storage at the boundary, in the documented layout.
    assert stored_records(hass_storage) == [
        [int(START.timestamp()), 500.0, 282.0, False]
    ]
    # One quarter-hour taught one factor: 500 W for 282 W/m².
    assert house.forecast.factors.by_hour[14] == 500.0 / 282.0
    await run(hass, freezer, 15 * 60)
    assert [r.start for r in house.forecast.history] == [
        START,
        START + timedelta(minutes=15),
    ]


async def test_the_record_holds_the_mean_of_changing_production(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Half the quarter-hour at 500 W, half at 700 W: about 600 W."""
    sim = dtu()
    _, house = await setup(
        hass, dtu_network, open_meteo, freezer, hass_storage, sim=sim
    )
    await run(hass, freezer, 450)
    sim.inverters[0].power = 500.0
    dtu_network.apply()
    await run(hass, freezer, 450)
    [record] = house.forecast.history
    assert 590.0 < record.production < 610.0


async def test_only_the_pv_inverters_of_the_house_count(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """A Battery-backed Inverter and an Inverter of nobody add nothing."""
    sim = dtu()
    sim.inverters[2].power = 900.0
    freezer.move_to(START)
    entry = await setup_entry(
        hass,
        dtu_network,
        sim,
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4, "1164a00ccd81"],
                battery_backed=["1164a00ccd81"],
            )
        ],
        open_meteo=open_meteo,
    )
    house = next(iter(entry.runtime_data.houses.values()))
    await run(hass, freezer, 15 * 60)
    [record] = house.forecast.history
    assert abs(record.production - 500.0) < 0.001


async def test_a_quarter_hour_with_too_few_samples_is_skipped(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Home Assistant being away for nine minutes leaves less than half covered."""
    _, house = await setup(hass, dtu_network, open_meteo, freezer, hass_storage)
    await run(hass, freezer, 4 * 60)
    await run(hass, freezer, 9 * 60, step=9 * 60)
    await run(hass, freezer, 2 * 60)
    assert house.forecast.history == ()
    # The next quarter-hour is complete again and is recorded.
    await run(hass, freezer, 15 * 60)
    assert [r.start for r in house.forecast.history] == [START + timedelta(minutes=15)]


async def test_a_quarter_hour_in_which_a_dtu_was_down_is_skipped(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Two minutes without the DTU spoil the quarter-hour although most is covered."""
    sim = dtu()
    _, house = await setup(
        hass, dtu_network, open_meteo, freezer, hass_storage, sim=sim
    )
    await run(hass, freezer, 5 * 60)
    sim.down = True
    dtu_network.apply()
    await run(hass, freezer, 2 * 60)
    sim.down = False
    dtu_network.apply()
    await run(hass, freezer, 8 * 60)
    assert house.forecast.history == ()
    await run(hass, freezer, 15 * 60)
    assert [r.start for r in house.forecast.history] == [START + timedelta(minutes=15)]


async def test_a_quarter_hour_without_a_forecast_is_recorded_but_teaches_nothing(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """With Open-Meteo down there is no irradiance to pair the production with."""
    open_meteo.down = True
    _, house = await setup(hass, dtu_network, open_meteo, freezer, hass_storage)
    await run(hass, freezer, 15 * 60)
    [record] = house.forecast.history
    assert record.irradiance is None
    assert house.forecast.factors is None
    assert house.forecast.history_days == 0


async def test_a_curtailed_quarter_hour_is_marked_and_does_not_change_the_factors(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Curtailing in the first quarter-hour only; the second one is clean."""
    _, house = await setup(
        hass, dtu_network, open_meteo, freezer, hass_storage, prepared_history()
    )
    before = house.forecast.factors
    assert before is not None
    hass.states.async_set(METER, "-500", {"unit_of_measurement": "W"})
    await house.control.async_set_curtailment(True)
    await hass.async_block_till_done()
    await run(hass, freezer, 14 * 60 + 50)
    assert house.control.curtailing
    assert dtu_network.limits()
    await house.control.async_set_curtailment(False)
    assert not house.control.curtailing
    await run(hass, freezer, 10)
    [curtailed] = house.forecast.history[-1:]
    assert curtailed.start == START
    assert curtailed.curtailed is True
    assert curtailed.irradiance == 282.0
    after = house.forecast.factors
    assert after.by_hour == pytest.approx(before.by_hour)
    assert after.overall == pytest.approx(before.overall)

    await run(hass, freezer, 15 * 60)
    clean = house.forecast.history[-1]
    assert clean.start == START + timedelta(minutes=15)
    assert clean.curtailed is False
    assert house.forecast.factors != before
    assert house.forecast.factors.by_hour[14] != house.forecast.factors.overall


async def test_the_history_survives_a_reload_of_the_entry(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Two recorded quarter-hours are still there, with the same factors, afterwards."""
    entry, house = await setup(hass, dtu_network, open_meteo, freezer, hass_storage)
    await run(hass, freezer, 30 * 60)
    history = house.forecast.history
    factors = house.forecast.factors
    assert len(history) == 2

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = next(iter(entry.runtime_data.houses.values())).forecast
    assert reloaded.history == history
    assert reloaded.factors == factors
    assert len(stored_records(hass_storage)) == 2


async def test_the_history_is_written_at_most_every_quarter_hour(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Between two boundaries the stored history does not change."""
    _, house = await setup(hass, dtu_network, open_meteo, freezer, hass_storage)
    await run(hass, freezer, 15 * 60)
    assert len(stored_records(hass_storage)) == 1
    await run(hass, freezer, 14 * 60)
    assert len(stored_records(hass_storage)) == 1
    assert len(house.forecast.history) == 1
    await run(hass, freezer, 60)
    assert len(stored_records(hass_storage)) == 2


async def test_records_older_than_sixty_days_are_dropped(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Of a record 61 days old and one 59 days old only the younger stays."""
    history = [
        (START - timedelta(days=61), 100.0, 300.0, False),
        (START - timedelta(days=59), 200.0, 300.0, False),
    ]
    _, house = await setup(
        hass, dtu_network, open_meteo, freezer, hass_storage, history
    )
    assert [r.start for r in house.forecast.history] == [START - timedelta(days=59)]
    await run(hass, freezer, 15 * 60, step=15 * 60)
    assert [r[0] for r in stored_records(hass_storage)] == [
        int((START - timedelta(days=59)).timestamp())
    ]


async def test_a_record_ages_out_while_running(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """A record 59 days and 23 hours old is dropped at the next quarter-hour."""
    old = START - timedelta(days=59, hours=23, minutes=45)
    _, house = await setup(
        hass, dtu_network, open_meteo, freezer, hass_storage, [(old, 1.0, 100.0, False)]
    )
    assert len(house.forecast.history) == 1
    await run(hass, freezer, 15 * 60, step=15 * 60)
    assert len(house.forecast.history) == 1
    await run(hass, freezer, 15 * 60, step=15 * 60)
    assert house.forecast.history == ()


async def test_usable_turns_on_when_the_history_is_sufficient(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Thirteen full days and seven records today; the eighth makes fourteen days."""
    today = datetime(2026, 10, 4, tzinfo=UTC)
    history = prepared_history(13) + day_records(today)[:7]
    await setup(hass, dtu_network, open_meteo, freezer, hass_storage, history)
    registry = er.async_get(hass)

    def read(platform: str, key: str) -> str:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"house-home_{key}")
        return hass.states.get(entity_id).state

    # 13 full days before today; today has 7 records. The 14th day is missing.
    assert read("binary_sensor", "pv_forecast_usable") == "off"
    assert read("sensor", "pv_forecast_history_days") == "13"
    await run(hass, freezer, 15 * 60)
    assert read("binary_sensor", "pv_forecast_usable") == "on"
    assert read("sensor", "pv_forecast_history_days") == "14"
