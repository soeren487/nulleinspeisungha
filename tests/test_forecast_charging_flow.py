"""Grid Charging with the PV Forecast, end to end with simulated Tibber, GX, DTU and
Open-Meteo, a seeded production and consumption history and a set clock.

The House is in Berlin (52.5 N, 13.4 E), UTC+2. The night starts at 22:00 on 4
October 2026. The AC Battery has 10 kWh and charges at 2100 W; the efficiency is
78 %; unless a test says so the GX reports no minimum charge level. Prices are
known to the end of the 5th: four quarter-hours of the night are worth charging
in (0.10 to 0.13 against 0.40) and, with the one cheap quarter-hour at 04:00 (not
worth it), are Discharge Blocks. The Expected Load is the fallback daily
consumption, spread evenly, unless a test seeds a history. Without PV 99 of the
104 quarter-hours to the end of the 5th are ones the House cannot choose the
price of.
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
    gx.no_minimum()  # these tests count the whole content, as a GX without one
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


DRAIN_SLOTS = 99
"""The quarter-hours from 22:00 to the end of the 5th that are not Discharge Blocks."""
GRID_PER_SLOT = 0.525
"""kWh from the grid in a quarter-hour at 2100 W."""


def _shortfall_without_pv(load_w: float, level: float = 20.0) -> float:
    """What the House buys at 0.40 with no PV: the load of 99 quarter-hours minus
    what the battery (no minimum) delivers."""
    return DRAIN_SLOTS * load_w * 0.25 / 1000.0 - 10.0 * level / 100.0 * ROOT


def _plan(hass: HomeAssistant) -> tuple[float, float, str]:
    """The shortfall, the energy to buy and the planned slots."""
    return (
        _kwh(hass, "expensive_shortfall"),
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
    """With 100 W round the clock and the recorded sunny day, little is short."""
    await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer, level=10.0
    )

    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"
    shortfall, to_buy, slots = _plan(hass)
    # the recorded sunny day (434 W at most, 70 % of it counted) leaves 0.07 kWh
    assert shortfall == pytest.approx(0.0698, abs=1e-3)
    assert _kwh(hass, "uncovered_shortfall") == 0.0
    # drawn from the grid: the shortfall plus the loss of the round trip
    assert to_buy == pytest.approx(shortfall / 0.78)
    # in the cheapest quarter-hour of all (02:00, 0.10), though far from the need
    assert slots == ["2026-10-05T00:00:00+00:00"]
    assert _kwh(hass, "reference_price") == pytest.approx(0.40)

    await _call(hass, "switch", "turn_off", "use_forecast")
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    without = _plan(hass)
    # 1 kWh above the minimum of 0 carries 0.88 kWh of the 2.475 kWh at the meter
    assert without[0] == pytest.approx(_shortfall_without_pv(100.0, 10.0))
    assert without[1] == pytest.approx(without[0] / 0.78)
    assert len(without[2]) == 4  # needs 4 * 0.525 kWh nearly
    assert shortfall < without[0]
    assert to_buy < without[1]


async def test_the_sensors_show_the_values_of_the_calculation(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The surplus equals what the pure calculation gives; the battery at sunrise
    is what 2 kWh, the night's load and the planned charging leave."""
    _entry, house, _gx = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    deadline = _deadline()
    load = {start: 100.0 for start, _ in house.expected_load.upcoming()}
    pv = {s.start: s.watts for s in house.forecast.slots}
    surplus = sunrise_energy.forecast_surplus(deadline, load, pv, 0.7)
    assert surplus is not None
    assert surplus > 0.5
    assert _kwh(hass, "forecast_surplus") == pytest.approx(surplus, abs=1e-3)
    # 22:00 to sunrise (07:15): 37 quarter-hours, 5 of them Discharge Blocks, and
    # no PV yet; the planned charging is stored before sunrise.
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        2.0 - 32 * 0.025 / ROOT + _kwh(hass, "energy_to_buy") * ROOT, abs=1e-3
    )


async def test_a_dull_forecast_buys_what_the_cheap_quarter_hours_allow(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """No sun: 300 W for the rest of the day cannot be bought for in four slots."""
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
    shortfall = _shortfall_without_pv(300.0)
    assert _kwh(hass, "expensive_shortfall") == pytest.approx(shortfall)
    assert _kwh(hass, "forecast_surplus") == 0.0
    # the plan equals the one without the forecast: all four worthwhile slots
    _, to_buy, slots = _plan(hass)
    assert to_buy == pytest.approx(4 * GRID_PER_SLOT)
    assert len(slots) == 4
    assert _kwh(hass, "uncovered_shortfall") == pytest.approx(
        shortfall - 4 * GRID_PER_SLOT * 0.78
    )


async def test_the_plan_never_takes_the_battery_above_the_charge_target(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """At 21 % (2.1 kWh) the last slot may only store what the battery can still
    hold after the House has used some in between."""
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
        {"entity_id": "number.home_charge_target", "value": 21},
        blocking=True,
    )
    await hass.async_block_till_done()
    drain = 0.075 / ROOT  # 300 W in a quarter-hour, taken out of the battery
    before_last = 2.0 - 20 * drain + 3 * GRID_PER_SLOT * ROOT
    # 02:00, 02:15 and 03:30 are full, 03:45 takes what is left up to 2.1 kWh
    assert _kwh(hass, "energy_to_buy") == pytest.approx(
        3 * GRID_PER_SLOT + (2.1 - before_last) / ROOT
    )
    assert _kwh(hass, "energy_to_buy") < 4 * GRID_PER_SLOT
    assert _kwh(hass, "energy_missing") == pytest.approx(0.1)


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
    before = (_plan(hass)[:2], _kwh(hass, "battery_at_sunrise"))
    surplus = _kwh(hass, "forecast_surplus")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.home_forecast_share", "value": 30},
        blocking=True,
    )
    await hass.async_block_till_done()
    after = (_plan(hass)[:2], _kwh(hass, "battery_at_sunrise"))
    assert after[0][0] == pytest.approx(0.843, abs=1e-3)
    assert after[0][0] > before[0][0]
    assert after[0][1] > before[0][1]
    assert after[1] > before[1]
    assert _kwh(hass, "forecast_surplus") < surplus


# -- no forecast bound -----------------------------------------------------------


async def test_without_a_usable_forecast_the_plan_is_pessimistic_about_pv(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """13 days of history: no PV is counted, so more is short and more is bought;
    the surplus the forecast would give is still shown."""
    await _setup(hass, dtu_network, tibber, open_meteo, hass_storage, freezer, days=13)
    assert _value(hass, "binary_sensor", "forecast_in_use") == "off"
    shortfall, to_buy, slots = _plan(hass)
    assert shortfall == pytest.approx(_shortfall_without_pv(100.0))
    assert to_buy == pytest.approx(shortfall / 0.78)
    assert len(slots) == 2  # 0.909 kWh from the grid: 02:00 and 02:15
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        2.0 - 32 * 0.025 / ROOT + to_buy * ROOT, abs=1e-3
    )
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
    shortfall, to_buy, slots = _plan(hass)
    assert shortfall == pytest.approx(_shortfall_without_pv(100.0))
    assert to_buy == pytest.approx(shortfall / 0.78)
    assert len(slots) == 2
    assert _kwh(hass, "forecast_surplus") > 0

    await _call(hass, "switch", "turn_on", "use_forecast")
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"
    assert _plan(hass)[0] < shortfall


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


async def test_a_load_known_only_for_part_of_the_horizon_shortens_the_plan(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The Expected Load reaches 48 hours; short of that, the plan ends where it
    ends, and the surplus after sunrise cannot be told."""
    _entry, house, _gx = await _setup(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    original = house.expected_load.upcoming
    house.expected_load.upcoming = lambda: original()[:20]  # type: ignore[method-assign]
    house.grid_charging._replan()
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "forecast_surplus") == "unknown"
    # 22:00 to 03:00: 20 quarter-hours, the two at 02:00 and 02:15 are blocks
    assert _kwh(hass, "expensive_shortfall") == 0.0
    assert _value(hass, "sensor", "grid_charging_state") == "no_shortfall"
    assert _kwh(hass, "battery_at_sunrise") == pytest.approx(
        2.0 - 18 * 0.025 / ROOT, abs=1e-3
    )


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
    assert _kwh(hass, "expensive_shortfall") == pytest.approx(
        _shortfall_without_pv(300.0), abs=1e-3
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
        dull=True,
    )
    # 150 W: the four worthwhile slots (4 * 0.525 kWh stored * e) are all used,
    # and 32 of the 37 quarter-hours to sunrise (no blocks: 33) take 0.0375 kWh.
    with_blocks = _kwh(hass, "battery_at_sunrise")
    assert with_blocks == pytest.approx(
        2.0 - 32 * 0.0375 / ROOT + 4 * GRID_PER_SLOT * ROOT, abs=1e-3
    )
    await _call(hass, "switch", "turn_off", "grid_charging")
    without_blocks = _kwh(hass, "battery_at_sunrise")
    # 04:00 is a cheap quarter-hour that is not planned: it no longer holds the battery
    assert without_blocks == pytest.approx(with_blocks - 0.0375 / ROOT, abs=1e-3)
    # the figures are there while Grid Charging is off
    assert _kwh(hass, "expensive_shortfall") > 0
    assert _value(hass, "binary_sensor", "forecast_in_use") == "on"


async def test_a_cheap_quarter_hour_after_sunrise_serves_the_shortfall_after_it(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """A worthwhile quarter-hour at 10:00 is used for the day after it; the
    dearer ones of the night (0.38 against a limit of 0.312) are not."""
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
    assert _plan(hass)[2] == ["2026-10-05T08:00:00+00:00"]
    assert _kwh(hass, "energy_to_buy") == pytest.approx(GRID_PER_SLOT)
    assert _value(hass, "sensor", "grid_charging_state") == "waiting"
    assert _value(hass, "binary_sensor", "charging_blocked_by_efficiency") == "off"
    # everything before 10:00 stays uncovered
    covered = GRID_PER_SLOT * 0.78
    assert _kwh(hass, "uncovered_shortfall") == pytest.approx(
        _kwh(hass, "expensive_shortfall") - covered
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


# -- the minimum charge level of the AC Battery ----------------------------------

AFTER_SUNRISE_PRICES = {**NIGHT_PRICES, "08:00": (0.12, "VERY_CHEAP")}
ACTIVE_MINIMUM = "system/0/Control/ActiveSocLimit"
CONFIGURED_MINIMUM = "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"


async def _minimum_house(
    hass, dtu_network, tibber, open_meteo, hass_storage, freezer
) -> SimGx:
    *_, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        open_meteo,
        hass_storage,
        freezer,
        daily_kwh=7.2,
        level=76.0,
        prices=AFTER_SUNRISE_PRICES,
    )
    return gx


async def test_a_raised_minimum_leaves_nothing_usable_and_the_plan_buys(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """76 % with an active minimum of 75 %: about 0 kWh usable, the plan buys."""
    gx = await _minimum_house(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    gx.set(ACTIVE_MINIMUM, None)
    gx.set(CONFIGURED_MINIMUM, None)
    await hass.async_block_till_done()
    before = (_kwh(hass, "battery_at_sunrise"), _plan(hass))

    gx.set(ACTIVE_MINIMUM, 75.0)
    await hass.async_block_till_done()
    after = (_kwh(hass, "battery_at_sunrise"), _plan(hass))

    # 7.6 kWh carry the House to the end of the day, 0.1 kWh do not
    assert before[0] > 1.0
    assert before[1][0] == 0.0
    assert before[1][1] == 0.0
    assert after[1][0] > 1.0
    assert after[0] < before[0]
    assert after[1][1] > 0.0
    assert len(after[1][2]) > 0
    assert _value(hass, "sensor", "ac_battery_minimum_charge_level") == "75.0"
    sensor = _state(hass, "sensor", "ac_battery_minimum_charge_level")
    assert sensor.attributes["source"] == "active"


async def test_the_configured_minimum_is_used_when_no_active_one_is_known(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    open_meteo: SimOpenMeteo,
    hass_storage: dict[str, Any],
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Without an active minimum the configured 20 % is the floor."""
    gx = await _minimum_house(
        hass, dtu_network, tibber, open_meteo, hass_storage, freezer
    )
    gx.set(CONFIGURED_MINIMUM, 20.0)
    await hass.async_block_till_done()
    with_configured = _kwh(hass, "battery_at_sunrise")
    shortfall_with_configured = _kwh(hass, "expensive_shortfall")
    sensor = _state(hass, "sensor", "ac_battery_minimum_charge_level")
    assert sensor.state == "20.0"
    assert sensor.attributes["source"] == "configured"
    gx.set(CONFIGURED_MINIMUM, None)
    await hass.async_block_till_done()
    without_floor = _kwh(hass, "battery_at_sunrise")
    assert _state(hass, "sensor", "ac_battery_minimum_charge_level").state == "0.0"
    # 20 % of 10 kWh is 2 kWh less usable
    assert with_configured < without_floor
    assert shortfall_with_configured > _kwh(hass, "expensive_shortfall")
