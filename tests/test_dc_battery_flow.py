"""DC Battery Support end to end: recording the output of Battery-backed Inverters
and the Charging Plan that counts on it.

The recording tests use a House with a PV Inverter (OMA, 200 W) and a
Battery-backed Inverter (BUERO4, 300 W). The planning tests are the House of
``test_forecast_charging_flow``: Berlin, 22:00 on 4 October 2026, a 10 kWh AC
Battery at 10 %, 100 W of Expected Load, a usable and sunny PV Forecast, five
Discharge Blocks; BUERO4 is Battery-backed with two DC Batteries of 1.6 kWh
(together) and a recorded night output of 300 W from 21:00 to 06:00.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung import solar
from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import (
    HOME_1,
    DtuNetwork,
    SimDtu,
    SimGx,
    SimHouse,
    SimOpenMeteo,
    SimTibber,
    seed_dc_output_history,
    seed_history,
    setup_entry,
)
from tests.test_forecast_flow import prepared_history
from tests.test_grid_charging_flow import (
    NIGHT_PRICES,
    _house,
    _local,
    _prices,
    _state,
    _value,
)

OMA, BUERO4 = "114183178036", "116191100801"
STORAGE_KEY = f"{DOMAIN}.dc_output_history_house-home"
START = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # 10:00 in Berlin
METER = "sensor.grid_meter"
ROOT = math.sqrt(0.78)
NOW = _local("2026-10-04T22:00")
BLOCK_HOURS = 5 * 0.25
SENSORS = ["sensor.dc_battery_1", "sensor.dc_battery_2"]


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


# -- recording ------------------------------------------------------------------


def _sim(oma: float = 200.0, buero4: float = 300.0) -> SimDtu:
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (oma, buero4, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


async def _record_setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    hass_storage: dict[str, Any],
    history: dict[str, list[tuple[datetime, float]]] | None = None,
    sim: SimDtu | None = None,
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(START)
    hass.states.async_set(METER, "300", {"unit_of_measurement": "W"})
    if history is not None:
        seed_dc_output_history(hass_storage, "house-home", history)
    entry = await setup_entry(
        hass,
        dtu_network,
        sim or _sim(),
        houses=[SimHouse("Home", inverters=[OMA, BUERO4], battery_backed=[BUERO4])],
    )
    return entry, next(iter(entry.runtime_data.houses.values()))


async def _run(hass: HomeAssistant, freezer, seconds: int, step: int = 10) -> None:
    for _ in range(seconds // step):
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _stored(hass_storage: dict[str, Any]) -> dict[str, list[list]]:
    return hass_storage[STORAGE_KEY]["data"]["series"]


async def test_a_night_of_steady_output_is_recorded_per_inverter(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Two quarter-hours of 300 W: records for the Battery-backed Inverter only."""
    _, house = await _record_setup(hass, dtu_network, freezer, hass_storage)
    assert house.dc_history.recorder.history(BUERO4) == ()
    await _run(hass, freezer, 30 * 60)
    records = house.dc_history.recorder.history(BUERO4)
    assert [r.start for r in records] == [START, START + timedelta(minutes=15)]
    assert [r.watts for r in records] == [pytest.approx(300.0)] * 2
    assert house.dc_history.recorder.series == (BUERO4,)
    assert _stored(hass_storage) == {
        BUERO4: [
            [int(START.timestamp()), pytest.approx(300.0)],
            [int((START + timedelta(minutes=15)).timestamp()), pytest.approx(300.0)],
        ]
    }


async def test_the_record_holds_the_mean_of_changing_output(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    sim = _sim()
    _, house = await _record_setup(hass, dtu_network, freezer, hass_storage, sim=sim)
    await _run(hass, freezer, 450)
    sim.inverters[1].power = 100.0
    dtu_network.apply()
    await _run(hass, freezer, 450)
    [record] = house.dc_history.recorder.history(BUERO4)
    assert 190.0 < record.watts < 210.0


async def test_a_quarter_hour_without_data_is_not_recorded(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    sim = _sim()
    _, house = await _record_setup(hass, dtu_network, freezer, hass_storage, sim=sim)
    sim.down = True
    dtu_network.apply()
    await _run(hass, freezer, 15 * 60)
    assert house.dc_history.recorder.history(BUERO4) == ()


async def test_the_history_survives_a_reload_of_the_entry(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    entry, house = await _record_setup(hass, dtu_network, freezer, hass_storage)
    await _run(hass, freezer, 30 * 60)
    history = house.dc_history.recorder.history(BUERO4)
    assert len(history) == 2
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = next(iter(entry.runtime_data.houses.values())).dc_history
    assert reloaded.recorder.history(BUERO4) == history


async def test_records_older_than_21_days_are_dropped(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    old = START - timedelta(days=22)
    recent = START - timedelta(days=20)
    entry, house = await _record_setup(
        hass,
        dtu_network,
        freezer,
        hass_storage,
        history={BUERO4: [(old, 100.0), (recent, 200.0)]},
    )
    assert [r.start for r in house.dc_history.recorder.history(BUERO4)] == [recent]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _stored(hass_storage) == {BUERO4: [[int(recent.timestamp()), 200.0]]}


async def test_the_usual_output_is_learned_from_the_history(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    """Three nights of 300 W at 10:00 local time (08:00 UTC) give 300 W there."""
    nights = [(START - timedelta(days=d), 300.0) for d in (1, 2, 3)]
    _, house = await _record_setup(
        hass, dtu_network, freezer, hass_storage, history={BUERO4: nights}
    )
    usual = house.dc_history.usual_output()
    assert set(usual) == {BUERO4}
    assert usual[BUERO4][40] == pytest.approx(300.0)
    assert usual[BUERO4][41] == 0.0


async def test_a_house_without_battery_backed_inverters_records_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, hass_storage, freezer
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(START)
    hass.states.async_set(METER, "300", {"unit_of_measurement": "W"})
    entry = await setup_entry(
        hass,
        dtu_network,
        _sim(),
        houses=[SimHouse("Home", inverters=[OMA, BUERO4])],
    )
    [house] = entry.runtime_data.houses.values()
    assert house.dc_history is None
    await _run(hass, freezer, 15 * 60)
    assert STORAGE_KEY not in hass_storage


# -- the Charging Plan ------------------------------------------------------------


def _night_history() -> dict[str, list[tuple[datetime, float]]]:
    """Ten nights of 300 W from 21:00 to 06:00 local (CEST) before 4 October."""
    records = []
    for day in range(1, 11):
        evening = _local("2026-10-04T21:00") - timedelta(days=day)
        records += [(evening + timedelta(minutes=15 * i), 300.0) for i in range(9 * 4)]
    return {BUERO4: records}


def _dtu() -> SimDtu:
    dtu = SimDtu.default()
    for inverter in dtu.inverters:
        inverter.reachable = True
        inverter.producing = False
        inverter.power = 0.0
    return dtu


async def _setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    freezer,
    *,
    sensors: dict[str, tuple[str, str] | None] | None = None,
    configured: bool = True,
    level: float = 10.0,
    night: dict[str, list[tuple[datetime, float]]] | None = None,
):
    """Set up at 22:00. ``sensors`` maps an entity id to (state, unit), or ``None``
    for a sensor that does not exist; the default is two full DC Batteries.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    _prices(tibber, tomorrow=NIGHT_PRICES)
    seed_history(hass_storage, "house-home", prepared_history(14))
    seed_dc_output_history(
        hass_storage, "house-home", night if night is not None else _night_history()
    )
    states = (
        sensors
        if sensors is not None
        else {SENSORS[0]: ("800", "Wh"), SENSORS[1]: ("0.8", "kWh")}
    )
    for entity_id, value in states.items():
        if value is not None:
            hass.states.async_set(
                entity_id, value[0], {"unit_of_measurement": value[1]}
            )
    gx = SimGx()
    gx.charge(level=level, power=0.0)
    hass.states.async_set(METER, "100", {"unit_of_measurement": "W"})
    entry = await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                battery_backed=[BUERO4],
                dc_batteries={BUERO4: SENSORS} if configured else {},
                tibber_home=HOME_1,
                gx=gx,
                capacity=10.0,
                grid_topic="grid/test/house",
                stored_switches={
                    "curtailment": "off",
                    "publish_grid_power": "on",
                    "grid_charging": "on",
                },
            )
        ],
        tibber=tibber,
        open_meteo=open_meteo,
    )
    house = _house(entry)
    house.expected_load.set_fallback_kwh(2.4)  # 100 W round the clock
    await hass.async_block_till_done()
    return entry, house


def _kwh(hass: HomeAssistant, key: str) -> float:
    return float(_value(hass, "sensor", key))


def _hours_to_sunrise() -> float:
    deadline = solar.next_sunrise(52.5, 13.4, NOW)
    return (deadline - NOW).total_seconds() / 3600.0


def _plan(hass: HomeAssistant) -> tuple[float, float]:
    """The energy missing and the energy to buy."""
    return _kwh(hass, "energy_missing"), _kwh(hass, "energy_to_buy")


def _content_without_support(level: float = 10.0) -> float:
    hours = _hours_to_sunrise() - BLOCK_HOURS
    return max(10.0 * level / 100.0 - 100.0 * hours / 1000.0 / ROOT, 0.0)


def _content_with_support(level: float = 10.0) -> float:
    """1.6 kWh * 0.85 at 300 W: 4 h and 18 quarters of the night up to 02:30 are
    covered. None of them is a Discharge Block but 02:00 and 02:15; 02:30 gets
    the remaining 0.01 kWh.
    """
    hours = _hours_to_sunrise() - BLOCK_HOURS
    drain = (100.0 * (hours - 4.0) - 10.0) / 1000.0 / ROOT
    return max(10.0 * level / 100.0 - drain, 0.0)


async def test_full_dc_batteries_make_the_plan_buy_less(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Empty DC Batteries: the plan of before. Full ones: the night is covered."""
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        sensors={SENSORS[0]: ("0", "Wh"), SENSORS[1]: ("0", "Wh")},
    )
    needed = _kwh(hass, "energy_needed_at_sunrise")
    assert needed == pytest.approx(0.7177, abs=1e-3)
    empty = _plan(hass)
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        _content_without_support(), abs=1e-3
    )
    assert empty[0] == pytest.approx(needed - _content_without_support(), abs=1e-3)
    assert empty[1] == pytest.approx(empty[0] / ROOT, abs=1e-3)
    assert _kwh(hass, "dc_battery_support") == 0.0

    hass.states.async_set(SENSORS[0], "800", {"unit_of_measurement": "Wh"})
    hass.states.async_set(SENSORS[1], "0.8", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    full = _plan(hass)
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(needed)
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        _content_with_support(), abs=1e-3
    )
    assert _kwh(hass, "dc_battery_support") == pytest.approx(1.36, abs=1e-3)
    assert _kwh(hass, "dc_battery_energy") == pytest.approx(1.6)
    assert full[0] == pytest.approx(needed - _content_with_support(), abs=1e-3)
    assert full[1] == pytest.approx(full[0] / ROOT, abs=1e-3)
    # the figures of the worked example: 0.095 kWh in the battery at sunrise become
    # 0.559 kWh, and the energy bought falls from 0.705 to 0.179 kWh
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(0.559, abs=1e-3)
    assert empty[1] == pytest.approx(0.705, abs=1e-3)
    assert full[1] == pytest.approx(0.179, abs=1e-3)
    assert full[0] < empty[0]
    assert full[1] < empty[1]


async def test_an_unavailable_sensor_counts_as_empty(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        sensors={SENSORS[0]: ("0", "Wh"), SENSORS[1]: ("0", "Wh")},
    )
    empty = _plan(hass)
    hass.states.async_set(SENSORS[0], "unavailable", {"unit_of_measurement": "Wh"})
    hass.states.async_set(SENSORS[1], "unknown", {"unit_of_measurement": "Wh"})
    await hass.async_block_till_done()
    assert _plan(hass) == pytest.approx(empty)
    assert _kwh(hass, "dc_battery_support") == 0.0
    assert _value(hass, "sensor", "dc_battery_energy") == "unknown"

    # one of two readable: only that one counts
    hass.states.async_set(SENSORS[0], "800", {"unit_of_measurement": "Wh"})
    await hass.async_block_till_done()
    assert _kwh(hass, "dc_battery_energy") == pytest.approx(0.8)
    assert _kwh(hass, "dc_battery_support") == pytest.approx(0.68)
    assert _plan(hass)[0] < empty[0]


async def test_a_sensor_that_does_not_exist_counts_as_empty(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        sensors={SENSORS[0]: None, SENSORS[1]: None},
    )
    assert _plan(hass)[0] == pytest.approx(
        _kwh(hass, "energy_needed_at_sunrise") - _content_without_support(), abs=1e-3
    )
    assert _value(hass, "sensor", "dc_battery_energy") == "unknown"


async def test_without_any_history_there_is_no_support(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Full DC Batteries, but nothing learned yet about how the Inverter delivers."""
    await _setup(hass, dtu_network, tibber, open_meteo, hass_storage, freezer, night={})
    assert _kwh(hass, "dc_battery_energy") == pytest.approx(1.6)
    assert _kwh(hass, "dc_battery_support") == 0.0
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        _content_without_support(), abs=1e-3
    )


async def test_a_house_without_sensors_plans_as_before(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Same House, no sensors named: the numbers of ``test_forecast_charging_flow``."""
    _entry, house = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer, configured=False
    )
    assert house.config.dc_batteries == {}
    assert house.grid_charging.dc_support is None
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(0.7177, abs=1e-3)
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        _content_without_support(), abs=1e-3
    )
    missing, to_buy = _plan(hass)
    assert missing == pytest.approx(0.7177 - _content_without_support(), abs=1e-3)
    assert to_buy == pytest.approx(missing / ROOT, abs=1e-3)
    registry = er.async_get(hass)
    for key in ("dc_battery_energy", "dc_battery_support"):
        assert (
            registry.async_get_entity_id("sensor", DOMAIN, f"house-home_{key}") is None
        )


async def test_the_plan_follows_a_sensor_that_moves_by_50_wh(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, house = await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        sensors={SENSORS[0]: ("0", "Wh"), SENSORS[1]: ("0", "Wh")},
    )
    plan = house.grid_charging.plan
    hass.states.async_set(SENSORS[0], "40", {"unit_of_measurement": "Wh"})
    await hass.async_block_till_done()
    assert house.grid_charging.plan is plan  # 0.04 kWh: not replanned
    assert _kwh(hass, "dc_battery_energy") == pytest.approx(0.04)  # but shown
    hass.states.async_set(SENSORS[0], "50", {"unit_of_measurement": "Wh"})
    await hass.async_block_till_done()
    assert house.grid_charging.plan is not plan
    assert _kwh(hass, "dc_battery_support") == pytest.approx(0.0425)
    replanned = house.grid_charging.plan
    hass.states.async_set(SENSORS[0], "70", {"unit_of_measurement": "Wh"})
    await hass.async_block_till_done()
    assert house.grid_charging.plan is replanned  # 0.02 kWh since the last plan


async def test_the_sensors_sit_on_the_charging_device_with_pinned_ids(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _setup(hass, dtu_network, tibber, open_meteo, hass_storage, freezer)
    energy = _state(hass, "sensor", "dc_battery_energy")
    support = _state(hass, "sensor", "dc_battery_support")
    assert energy.attributes["unit_of_measurement"] == "kWh"
    assert energy.attributes["device_class"] == "energy_storage"
    assert energy.attributes["readable_sensors"] == 2
    assert support.attributes["unit_of_measurement"] == "kWh"
    registry = er.async_get(hass)
    charging = registry.async_get("sensor.home_dc_battery_energy")
    other = registry.async_get("sensor.home_energy_to_buy")
    assert charging is not None
    assert other is not None
    assert charging.device_id == other.device_id


async def test_the_configuration_survives_a_reload(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, _ = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    support = _kwh(hass, "dc_battery_support")
    assert support > 1.0
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _kwh(hass, "dc_battery_support") == pytest.approx(support)
