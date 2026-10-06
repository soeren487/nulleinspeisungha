"""The inverter control of a House sits on a device of its own."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

OMA, BUERO4 = "114183178036", "116191100801"

MOVED = [
    ("switch", "curtailment"),
    ("number", "feed_in_setpoint"),
    ("number", "update_interval"),
    ("number", "tolerance_band"),
    ("number", "limit_floor"),
    ("number", "limit_slew_rate"),
    ("number", "response_reserve"),
    ("select", "on_failure"),
    ("sensor", "control_state"),
    ("sensor", "inverter_limit"),
    ("sensor", "battery_backed_limit"),
]
STAYING = [
    ("switch", "publish_grid_power"),
    ("number", "maximum_charge_power"),
    ("number", "fallback_daily_consumption"),
    ("sensor", "grid_power"),
    ("sensor", "inverter_production"),
    ("sensor", "battery_soc"),
    ("sensor", "expected_load"),
    ("sensor", "pv_forecast_power"),
    ("binary_sensor", "battery_connected"),
    ("binary_sensor", "pv_forecast_usable"),
]
NEW_IDS = {
    "switch.home_curtailment": "house-home_curtailment",
    "number.home_feed_in_setpoint": "house-home_feed_in_setpoint",
    "number.home_update_interval": "house-home_update_interval",
    "number.home_tolerance_band": "house-home_tolerance_band",
    "number.home_limit_floor": "house-home_limit_floor",
    "number.home_limit_slew_rate": "house-home_limit_slew_rate",
    "number.home_response_reserve": "house-home_response_reserve",
    "select.home_on_failure": "house-home_on_failure",
    "sensor.home_control_state": "house-home_control_state",
    "sensor.home_inverter_limit": "house-home_inverter_limit",
    "sensor.home_battery_backed_inverter_limit": "house-home_battery_backed_limit",
}


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


async def _setup(hass: HomeAssistant, dtu_network: DtuNetwork) -> ConfigEntry:
    return await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                battery_backed=[OMA],
                gx=SimGx(),
                grid_topic="grid/test/house",
            )
        ],
    )


def _devices(hass: HomeAssistant):
    registry = dr.async_get(hass)
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    house = registry.async_get_device_by_identifier((DOMAIN, "house-home"), entry_id)
    control = registry.async_get_device_by_identifier(
        (DOMAIN, "house-home_control"), entry_id
    )
    return house, control


def _entry(hass: HomeAssistant, platform: str, key: str) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"house-home_{key}")
    assert entity_id is not None, f"no entity {platform} {key}"
    return registry.async_get(entity_id)


async def test_control_device_below_the_house_device(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """The control device is named after the House and hangs below it."""
    await _setup(hass, dtu_network)
    house, control = _devices(hass)
    assert house is not None
    assert control is not None
    assert control.name == "Home Inverter control"
    assert control.manufacturer == "Nulleinspeisung"
    assert control.model == "Inverter control"
    assert control.via_device_id == house.id
    assert control.config_subentry_id == house.config_subentry_id
    assert control.config_subentry_id is not None


async def test_control_device_is_named_in_german(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """In a German Home Assistant the device reads "<House> Wechselrichter-Regelung"."""
    hass.config.language = "de"
    await _setup(hass, dtu_network)
    _, control = _devices(hass)
    assert control.name == "Home Wechselrichter-Regelung"
    state = hass.states.get("number.home_tolerance_band")
    assert state.name == "Home Wechselrichter-Regelung Toleranzband"


@pytest.mark.parametrize(("platform", "key"), MOVED)
async def test_moved_entity_is_on_the_control_device(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    mqtt_mock: MagicMock,
    platform: str,
    key: str,
) -> None:
    """Switch, settings and state of the control are on the control device."""
    await _setup(hass, dtu_network)
    _, control = _devices(hass)
    assert _entry(hass, platform, key).device_id == control.id


@pytest.mark.parametrize(("platform", "key"), STAYING)
async def test_other_entity_stays_on_the_house_device(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    mqtt_mock: MagicMock,
    platform: str,
    key: str,
) -> None:
    """Everything else is still on the House device."""
    await _setup(hass, dtu_network)
    house, _ = _devices(hass)
    assert _entry(hass, platform, key).device_id == house.id


async def test_a_new_house_keeps_the_entity_ids_of_before(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """The device rename does not change the entity ids of a new House."""
    await _setup(hass, dtu_network)
    registry = er.async_get(hass)
    for entity_id, unique_id in NEW_IDS.items():
        platform = entity_id.split(".")[0]
        assert registry.async_get_entity_id(platform, DOMAIN, unique_id) == entity_id


async def test_existing_entities_move_and_keep_their_ids(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Set up again, entities of the released layout land on the control device."""
    entry = await _setup(hass, dtu_network)
    house, control = _devices(hass)
    registry = er.async_get(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Put the registries back to what the released version had left behind.
    before = {}
    for entity_id in NEW_IDS:
        before[entity_id] = registry.async_get(entity_id)
        registry.async_update_entity(entity_id, device_id=house.id)
    dr.async_get(hass).async_remove_device(control.id)
    assert _devices(hass)[1] is None

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    house_now, control_now = _devices(hass)
    assert house_now.id == house.id
    assert control_now is not None
    for entity_id, old in before.items():
        moved = registry.async_get(entity_id)
        assert moved is not None, entity_id
        assert moved.id == old.id
        assert moved.device_id == control_now.id


async def test_removing_the_house_removes_both_devices(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Both devices belong to the House's subentry."""
    entry = await _setup(hass, dtu_network)
    assert all(_devices(hass))
    subentry = next(s for s in entry.subentries.values() if s.title == "Home")
    hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()
    assert _devices(hass) == (None, None)
