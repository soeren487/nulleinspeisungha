"""Grid Charging with the PV Forecast, end to end with simulated Tibber, GX, DTU and
Open-Meteo, a seeded production and consumption history and a set clock.

The House is in Berlin (52.5 N, 13.4 E), UTC+2. The night starts at 22:00 on 4
October 2026; the next sunrise is the deadline. The AC Battery has 10 kWh and
charges at 2100 W; the efficiency is 78 %. Four quarter-hours of the night are
worth charging in (0.10 to 0.13 against a reference price of 0.40); they and
the one cheap quarter-hour at 04:00 (not worth it, but a Discharge Block) are
the Discharge Blocks. The Expected Load is the fallback daily consumption, spread
evenly, unless a test seeds a history.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.nulleinspeisung import solar, sunrise_energy
from tests.conftest import (
    HOME_1,
    DtuNetwork,
    SimDtu,
    SimGx,
    SimHouse,
    SimOpenMeteo,
    SimTibber,
    seed_history,
    seed_load_history,
    setup_entry,
)
from tests.test_forecast_flow import prepared_history
from tests.test_grid_charging_flow import (
    BLOCKED_PRICES,
    NIGHT_PRICES,
    _call,
    _house,
    _local,
    _prices,
    _state,
    _value,
)

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
ROOT = math.sqrt(0.78)
NOW = _local("2026-10-04T22:00")
BLOCK_HOURS = 5 * 0.25
"""Five quarter-hours of the night are Discharge Blocks."""


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _dtu() -> SimDtu:
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (0.0, 0.0), strict=False):
        inverter.reachable = True
        inverter.producing = False
        inverter.power = power
    return dtu


async def _setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    freezer,
    *,
    daily_kwh: float = 2.4,
    level: float = 20.0,
    days: int = 14,
    dull: bool = False,
    switches: dict[str, str | None] | None = None,
    prices: dict[str, tuple[float, str]] | None = None,
    load_history: list[tuple[datetime, float]] | None = None,
):
    """Set up at 22:00 with ``days`` of production history and a fallback load.

    ``daily_kwh`` of 2.4 is 100 W round the clock.
    """
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    _prices(tibber, tomorrow=prices if prices is not None else NIGHT_PRICES)
    if dull:
        open_meteo.irradiance[:] = [0.0] * len(open_meteo.irradiance)
    seed_history(hass_storage, "house-home", prepared_history(days))
    if load_history is not None:
        seed_load_history(hass_storage, "house-home", load_history)
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
                tibber_home=HOME_1,
                gx=gx,
                capacity=10.0,
                grid_topic="grid/test/house",
                stored_switches={
                    "curtailment": "off",
                    "publish_grid_power": "on",
                    "grid_charging": "on",
                    **(switches or {}),
                },
            )
        ],
        tibber=tibber,
        open_meteo=open_meteo,
    )
    house = _house(entry)
    house.expected_load.set_fallback_kwh(daily_kwh)
    await hass.async_block_till_done()
    return entry, house, gx


def _kwh(hass: HomeAssistant, key: str) -> float:
    return float(_value(hass, "sensor", key))


def _deadline() -> datetime:
    return solar.next_sunrise(52.5, 13.4, NOW)


def _hours_to_sunrise() -> float:
    return (_deadline() - NOW).total_seconds() / 3600.0


def _content(load_w: float, level: float = 20.0) -> float:
    """What the battery holds at sunrise under a flat load and five blocked slots."""
    hours = _hours_to_sunrise() - BLOCK_HOURS
    return max(10.0 * level / 100.0 - load_w * hours / 1000.0 / ROOT, 0.0)


def _plan(hass: HomeAssistant) -> tuple[float, float, str]:
    """The energy missing, the energy to buy and the planned slots."""
    return (
        _kwh(hass, "energy_missing"),
        _kwh(hass, "energy_to_buy"),
        _state(hass, "sensor", "next_charging_start").attributes.get("slots", []),
    )


# -- a usable forecast ---------------------------------------------------------


async def test_a_sunny_forecast_buys_less_than_without_it(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """With 100 W round the clock and the recorded sunny day, little must be stored."""
    await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer, level=10.0
    )

    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"
    with_forecast = _plan(hass)
    store = _kwh(hass, "energy_needed_at_sunrise") - _kwh(hass, "battery_at_sunrise")
    # the recorded sunny day: 0.7177 kWh needed at sunrise (see the sensor test)
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(0.7177, abs=1e-3)
    assert store == pytest.approx(0.7177 - _content(100.0, 10.0), abs=1e-3)
    assert with_forecast[0] == pytest.approx(store)
    # drawn from the grid: the stored energy plus the loss of charging
    assert with_forecast[1] == pytest.approx(store / ROOT)
    assert len(with_forecast[2]) == 2  # the two cheapest quarter-hours do it

    await _call(hass, "switch", "turn_off", "use_forecast")
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    without = _plan(hass)
    # 9 kWh are missing; the four worthwhile quarter-hours draw 2.1 kWh
    assert without[0] == pytest.approx(9.0)
    assert without[1] == pytest.approx(2.1)
    assert len(without[2]) == 4
    assert with_forecast[0] < without[0]
    assert with_forecast[1] < without[1]


async def test_the_sensors_show_the_values_of_the_calculation(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The three sensors equal what the pure calculation gives for the same inputs."""
    _entry, house, _gx = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    deadline = _deadline()
    load = {start: 100.0 for start, _ in house.expected_load.upcoming()}
    pv = {s.start: s.watts for s in house.forecast.slots}
    # no qualifying quarter-hour after sunrise: the period is 24 hours
    end = deadline + timedelta(hours=24)
    needed = sunrise_energy.energy_needed_at_sunrise(deadline, load, pv, 0.7, end, 0.78)
    assert needed is not None
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(needed, abs=1e-3)
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(_content(100.0), abs=1e-3)
    surplus = sunrise_energy.forecast_surplus(deadline, load, pv, 0.7)
    assert surplus is not None
    assert surplus > 0.5
    assert _kwh(hass, "forecast_surplus") == pytest.approx(surplus, abs=1e-3)
    # the peak is the deficit before the sun is high enough, and the night after it
    assert needed < 100.0 * 24 / 1000 / ROOT


async def test_a_dull_forecast_buys_up_to_the_target(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """No sun: 300 W for 24 hours need more than the 8 kWh the target allows."""
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        daily_kwh=7.2,
        dull=True,
    )
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"
    needed = 300.0 * 24 / 1000 / ROOT
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(needed, abs=1e-3)
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(_content(300.0), abs=1e-3)
    assert _kwh(hass, "forecast_surplus") == 0.0
    assert needed - _content(300.0) > 8.0
    # the plan equals the one without the forecast: up to the Charge Target
    missing, to_buy, slots = _plan(hass)
    assert missing == pytest.approx(8.0)
    assert to_buy == pytest.approx(2.1)
    assert len(slots) == 4


async def test_the_plan_never_exceeds_the_energy_missing_to_the_target(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A dull day needs 8 kWh at least, but only 3 kWh are missing to 50 %."""
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        daily_kwh=7.2,
        dull=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.home_charge_target", "value": 50},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _kwh(hass, "energy_needed_at_sunrise") > 8.0
    assert _kwh(hass, "energy_missing") == pytest.approx(3.0)


async def test_a_lower_counted_share_buys_more(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The share changes the sensors and the plan: less sun counted, more bought."""
    await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer, level=10.0
    )
    assert float(_value(hass, "number", "forecast_share")) == 70.0
    before = (_plan(hass)[0], _kwh(hass, "energy_needed_at_sunrise"))
    surplus = _kwh(hass, "forecast_surplus")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.home_forecast_share", "value": 30},
        blocking=True,
    )
    await hass.async_block_till_done()
    after = (_plan(hass)[0], _kwh(hass, "energy_needed_at_sunrise"))
    assert after[1] > before[1]
    assert after[0] > before[0]
    assert _kwh(hass, "forecast_surplus") < surplus


# -- no forecast bound -----------------------------------------------------------


async def test_without_a_usable_forecast_the_plan_is_the_one_without_it(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """13 days of history: the plan fills to the target, the sensors still work."""
    await _setup(hass, dtu_network, tibber, open_meteo, hass_storage, freezer, days=13)
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    missing, to_buy, slots = _plan(hass)
    assert missing == pytest.approx(8.0)
    assert to_buy == pytest.approx(2.1)
    assert len(slots) == 4
    # the owner can watch the figures before relying on them
    assert _kwh(hass, "energy_needed_at_sunrise") > 0
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(_content(100.0), abs=1e-3)
    assert _kwh(hass, "forecast_surplus") > 0


async def test_with_the_switch_off_the_plan_is_the_one_without_the_forecast(
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
        switches={"use_forecast": "off"},
    )
    assert _value(hass, "switch", "use_forecast") == "off"
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    missing, to_buy, slots = _plan(hass)
    assert missing == pytest.approx(8.0)
    assert to_buy == pytest.approx(2.1)
    assert len(slots) == 4
    assert _kwh(hass, "energy_needed_at_sunrise") > 0

    await _call(hass, "switch", "turn_on", "use_forecast")
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"
    assert _plan(hass)[0] < 8.0


async def test_the_switch_is_on_by_default(
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
        switches={"use_forecast": None},
    )
    assert _value(hass, "switch", "use_forecast") == "on"
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"


async def test_a_load_unknown_for_the_period_gives_unknown_and_no_bound(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The Expected Load reaches 48 hours; short of the period, nothing is bound."""
    _entry, house, _gx = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    assert house.grid_charging.forecast_in_use
    original = house.expected_load.upcoming
    house.expected_load.upcoming = lambda: original()[:20]  # type: ignore[method-assign]
    house.grid_charging._replan()
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "energy_needed_at_sunrise") == "unknown"
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    assert _plan(hass)[0] == pytest.approx(8.0)


async def test_a_learned_load_decides(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Eight days of 300 W replace the fallback of 100 W."""
    days = []
    midnight = datetime(2026, 10, 4, tzinfo=UTC)
    for day in range(1, 9):
        first = midnight - timedelta(days=day)
        days += [(first + timedelta(minutes=15 * i), 300.0) for i in range(96)]
    _entry, house, _gx = await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        load_history=days,
        dull=True,
    )
    house.expected_load.set_fallback_kwh(2.4)
    await hass.async_block_till_done()
    assert house.expected_load.learned
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(
        300.0 * 24 / 1000 / ROOT, abs=1e-3
    )


# -- the blocks ----------------------------------------------------------------------


async def test_discharge_blocks_keep_the_content_at_sunrise_higher(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """With Grid Charging off no block applies, so the battery empties longer."""
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        daily_kwh=3.6,
    )
    with_blocks = _kwh(hass, "battery_at_sunrise")
    assert with_blocks == pytest.approx(_content(150.0), abs=1e-3)
    await _call(hass, "switch", "turn_off", "grid_charging")
    without_blocks = _kwh(hass, "battery_at_sunrise")
    assert without_blocks == pytest.approx(
        max(2.0 - 150.0 * _hours_to_sunrise() / 1000 / ROOT, 0.0), abs=1e-3
    )
    assert without_blocks < with_blocks
    # the figures are there while Grid Charging is off
    assert _kwh(hass, "energy_needed_at_sunrise") > 0
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"


async def test_the_period_ends_at_the_next_qualifying_quarter_hour(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A cheap quarter-hour at 10:00 after sunrise limits the bridge to that."""
    prices = {**BLOCKED_PRICES, "10:00": (0.30, "CHEAP")}
    await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        daily_kwh=7.2,
        dull=True,
        prices=prices,
    )
    hours = (_local("2026-10-05T10:00") - _deadline()).total_seconds() / 3600.0
    assert _kwh(hass, "energy_needed_at_sunrise") == pytest.approx(
        300.0 * hours / 1000 / ROOT, abs=1e-3
    )


# -- settings across a reload ----------------------------------------------------------


async def test_the_switch_and_the_number_survive_a_reload(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, *_ = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    await _call(hass, "switch", "turn_off", "use_forecast")
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.home_forecast_share", "value": 45},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _value(hass, "switch", "use_forecast") == "off"
    assert float(_value(hass, "number", "forecast_share")) == 45.0
    assert _house(entry).grid_charging.forecast_share == 45.0
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"


async def test_the_stored_settings_are_restored(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                _number_state("number.home_forecast_share", 90),
                {
                    "native_max_value": 100,
                    "native_min_value": 10,
                    "native_step": 5,
                    "native_unit_of_measurement": "%",
                    "native_value": 90,
                },
            ),
        ],
    )
    await _setup(hass, dtu_network, tibber, open_meteo, hass_storage, freezer)
    assert float(_value(hass, "number", "forecast_share")) == 90.0


def _number_state(entity_id: str, value: float):
    from homeassistant.core import State

    return State(entity_id, str(value))
