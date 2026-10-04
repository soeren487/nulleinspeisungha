"""Tests for the entities of a House, as the owner sees them."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import (
    DOMAIN,
    DTU_UPDATE_INTERVAL,
    SIGN_EXPORT,
    SIGN_IMPORT,
)
from tests.conftest import DtuNetwork, SimDtu, SimHouse, second_dtu, setup_entry

GARAGE_1, GARAGE_2 = "200000000001", "200000000002"
OMA, BUERO4, BUERO5 = "114183178036", "116191100801", "1164a00ccd81"


def _state(hass: HomeAssistant, house: str, key: str) -> str:
    """State of a House sensor by the House's unique id and the entity key."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"house-{house}_{key}"
    )
    assert entity_id is not None, f"no entity {house} {key}"
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def _refresh(hass: HomeAssistant, freezer) -> None:
    """Let the DTU coordinators poll once more."""
    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_house_device_and_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House appears as its own device with its sensors."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", inverters=[OMA, BUERO4], battery_backed=[OMA])],
    )
    assert entry.state is ConfigEntryState.LOADED
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "house-home"), entry.entry_id
    )
    assert device is not None
    assert device.name == "Home"
    assert device.manufacturer == "Nulleinspeisung"
    assert device.model == "House"
    assert _state(hass, "home", "inverter_count") == "2"
    for key in (
        "grid_power",
        "inverter_production",
        "pv_production",
        "battery_backed_production",
    ):
        _state(hass, "home", key)
    registry = er.async_get(hass)
    sensor = registry.async_get(
        registry.async_get_entity_id("sensor", DOMAIN, "house-home_grid_power")
    )
    assert sensor.translation_key == "grid_power"
    assert sensor.device_id == device.id


@pytest.mark.parametrize(
    ("sign", "value", "unit", "expected"),
    [
        (SIGN_IMPORT, "300", "W", 300.0),
        (SIGN_IMPORT, "-120", "W", -120.0),
        (SIGN_EXPORT, "300", "W", -300.0),
        (SIGN_EXPORT, "-120", "W", 120.0),
        (SIGN_IMPORT, "1.2", "kW", 1200.0),
        (SIGN_EXPORT, "1.2", "kW", -1200.0),
    ],
)
async def test_grid_power_follows_grid_meter(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    sign: str,
    value: str,
    unit: str,
    expected: float,
) -> None:
    """Grid Power shows the meter in W, import positive, per sign option."""
    hass.states.async_set("sensor.grid_meter", "0", {"unit_of_measurement": "W"})
    await setup_entry(
        hass, dtu_network, SimDtu.default(), houses=[SimHouse("Home", sign=sign)]
    )
    hass.states.async_set("sensor.grid_meter", value, {"unit_of_measurement": unit})
    await hass.async_block_till_done()
    assert float(_state(hass, "home", "grid_power")) == expected


async def test_grid_power_unavailable_cases(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Grid Power is unavailable for a missing, unavailable, unknown or odd meter."""
    await setup_entry(hass, dtu_network, SimDtu.default(), houses=[SimHouse("Home")])
    assert _state(hass, "home", "grid_power") == "unavailable"
    for bad in ("unavailable", "unknown", "garbage"):
        hass.states.async_set("sensor.grid_meter", "50", {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        assert float(_state(hass, "home", "grid_power")) == 50.0
        hass.states.async_set("sensor.grid_meter", bad, {"unit_of_measurement": "W"})
        await hass.async_block_till_done()
        assert _state(hass, "home", "grid_power") == "unavailable"
    hass.states.async_set("sensor.grid_meter", "50", {"unit_of_measurement": "W"})
    hass.states.async_remove("sensor.grid_meter")
    await hass.async_block_till_done()
    assert _state(hass, "home", "grid_power") == "unavailable"


async def test_production_sums_split_and_follow_refresh(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Production follows the DTUs across refreshes and splits PV and battery."""
    one, two = SimDtu.default(), second_dtu()
    one.inverters[0].power = 100.0
    one.inverters[1].power = 200.0
    two.inverters[0].power = 50.0
    two.inverters[1].power = 25.0
    await setup_entry(
        hass,
        dtu_network,
        one,
        two,
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4, GARAGE_1, GARAGE_2],
                battery_backed=[BUERO4, GARAGE_2],
            )
        ],
    )
    assert float(_state(hass, "home", "inverter_production")) == 375.0
    assert float(_state(hass, "home", "pv_production")) == 150.0
    assert float(_state(hass, "home", "battery_backed_production")) == 225.0

    one.inverters[0].power = 300.0
    dtu_network.apply()
    await _refresh(hass, freezer)
    assert float(_state(hass, "home", "inverter_production")) == 575.0
    assert float(_state(hass, "home", "pv_production")) == 350.0


async def test_down_dtu_removes_only_its_inverters(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """When one DTU stops answering, only its Inverters stop contributing."""
    one, two = SimDtu.default(), second_dtu()
    one.inverters[0].power = 100.0
    two.inverters[0].power = 50.0
    await setup_entry(
        hass,
        dtu_network,
        one,
        two,
        houses=[SimHouse("Home", inverters=[OMA, GARAGE_1])],
    )
    assert float(_state(hass, "home", "inverter_production")) == 150.0
    two.down = True
    dtu_network.apply()
    await _refresh(hass, freezer)
    assert float(_state(hass, "home", "inverter_production")) == 100.0
    one.down = True
    dtu_network.apply()
    await _refresh(hass, freezer)
    assert _state(hass, "home", "inverter_production") == "unavailable"
    two.down = False
    one.down = False
    dtu_network.apply()
    await _refresh(hass, freezer)
    assert float(_state(hass, "home", "inverter_production")) == 150.0


async def test_no_battery_backed_inverters_means_unavailable(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A group without Inverters has no production value."""
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", inverters=[OMA])],
    )
    assert _state(hass, "home", "battery_backed_production") == "unavailable"
    assert float(_state(hass, "home", "pv_production")) == 0.0


async def test_houses_do_not_influence_each_other(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Each House has its own Grid Meter and its own Inverters."""
    dtu = SimDtu.default()
    dtu.inverters[0].power = 100.0
    dtu.inverters[1].power = 200.0
    await setup_entry(
        hass,
        dtu_network,
        dtu,
        houses=[
            SimHouse("A", grid_meter="sensor.meter_a", inverters=[OMA]),
            SimHouse(
                "B",
                grid_meter="sensor.meter_b",
                sign=SIGN_EXPORT,
                inverters=[BUERO4],
            ),
        ],
    )
    hass.states.async_set("sensor.meter_a", "10", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.meter_b", "20", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(_state(hass, "a", "grid_power")) == 10.0
    assert float(_state(hass, "b", "grid_power")) == -20.0
    assert float(_state(hass, "a", "inverter_production")) == 100.0
    assert float(_state(hass, "b", "inverter_production")) == 200.0
    hass.states.async_set("sensor.meter_a", "11", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(_state(hass, "b", "grid_power")) == -20.0
    assert float(_state(hass, "a", "grid_power")) == 11.0


async def test_unassigned_inverters_keep_their_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Inverters not in any House keep their own sensors, as before."""
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", inverters=[OMA])],
    )
    registry = er.async_get(hass)
    for serial in (OMA, BUERO4, BUERO5):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{serial}_power")
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{serial}_limit")
    assert float(_state(hass, "home", "inverter_count")) == 1


async def test_house_without_inverters(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House with no Inverters shows a count of zero and no production."""
    await setup_entry(hass, dtu_network, SimDtu.default(), houses=[SimHouse("Home")])
    assert _state(hass, "home", "inverter_count") == "0"
    assert _state(hass, "home", "inverter_production") == "unavailable"


async def test_removing_a_dtu_keeps_the_assignment(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Removing a DTU does not drop its Inverters from the House."""
    one, two = SimDtu.default(), second_dtu()
    entry = await setup_entry(
        hass, dtu_network, one, two, houses=[SimHouse("Home", inverters=[GARAGE_1])]
    )
    dtu_subentry = next(
        s for s in entry.subentries.values() if s.unique_id == two.serial
    )
    hass.config_entries.async_remove_subentry(entry, dtu_subentry.subentry_id)
    await hass.async_block_till_done()
    house = next(s for s in entry.subentries.values() if s.title == "Home")
    assert list(house.data["inverters"]) == [GARAGE_1]
    assert _state(hass, "home", "inverter_production") == "unavailable"
    assert _state(hass, "home", "inverter_count") == "1"
