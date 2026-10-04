"""Tests for the House subentry flow (adding and reconfiguring Houses)."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import selector

from custom_components.nulleinspeisung.const import (
    SIGN_EXPORT,
    SIGN_IMPORT,
    SUBENTRY_TYPE_HOUSE,
)
from tests.conftest import DtuNetwork, SimDtu, SimHouse, second_dtu, setup_entry

OMA, BUERO4, BUERO5 = "114183178036", "116191100801", "1164a00ccd81"
GARAGE_1, GARAGE_2 = "200000000001", "200000000002"


async def _start(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry | None = None
) -> ConfigFlowResult:
    """Open the House flow, to add or to reconfigure."""
    if subentry is None:
        return await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_HOUSE), context={"source": "user"}
        )
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_HOUSE),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )


def _first(
    name: str, meter: str = "sensor.grid_meter", sign: str = SIGN_IMPORT
) -> dict[str, Any]:
    """Input for the first step."""
    return {
        "name": name,
        "location": {"latitude": 50.0, "longitude": 8.0, "radius": 100},
        "grid_meter": meter,
        "grid_meter_sign": sign,
    }


async def _next(hass: HomeAssistant, result: ConfigFlowResult, data: dict[str, Any]):
    """Submit one step."""
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


def _offered(result: ConfigFlowResult, field: str = "inverters") -> list[str]:
    """Serials offered in an Inverter selection step."""
    for key, value in result["data_schema"].schema.items():
        if key == field:
            assert isinstance(value, selector.SelectSelector)
            return [o["value"] for o in value.config["options"]]
    raise AssertionError(field)


def _defaults(result: ConfigFlowResult) -> dict[str, Any]:
    """Defaults the form shows, by field."""
    return {
        k.schema: k.default()
        for k in result["data_schema"].schema
        if k.default is not vol.UNDEFINED
    }


def _labels(result: ConfigFlowResult) -> dict[str, str]:
    """Labels offered in the Inverter step."""
    for key, value in result["data_schema"].schema.items():
        if key == "inverters":
            return {o["value"]: o["label"] for o in value.config["options"]}
    raise AssertionError


def _house(entry: ConfigEntry, name: str) -> ConfigSubentry:
    """The House subentry with this name."""
    return next(
        s
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_TYPE_HOUSE and s.title == name
    )


async def _create(
    hass: HomeAssistant,
    entry: ConfigEntry,
    name: str,
    inverters: list[str],
    battery_backed: list[str],
    **first: Any,
) -> ConfigFlowResult:
    """Run the whole flow for a new House."""
    result = await _start(hass, entry)
    result = await _next(hass, result, _first(name, **first))
    result = await _next(hass, result, {"inverters": inverters})
    if inverters:
        result = await _next(hass, result, {"battery_backed": battery_backed})
    await hass.async_block_till_done()
    return result


async def test_create_house_through_three_steps(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The three steps store the House and the owner can see it listed."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _start(hass, entry)
    assert result["step_id"] == "user"
    defaults = _defaults(result)
    assert defaults["location"]["latitude"] == hass.config.latitude
    assert defaults["grid_meter_sign"] == SIGN_IMPORT

    result = await _next(hass, result, _first("Home", sign=SIGN_EXPORT))
    assert result["step_id"] == "inverters"
    assert sorted(_offered(result)) == sorted([OMA, BUERO4, BUERO5])
    assert _labels(result)[BUERO4] == "Büro 4 (OpenDTU-Buero, HM-1500-4T)"
    result = await _next(hass, result, {"inverters": [OMA, BUERO4]})
    assert result["step_id"] == "battery_backed"
    assert sorted(_offered(result, "battery_backed")) == sorted([OMA, BUERO4])
    result = await _next(hass, result, {"battery_backed": [BUERO4]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    await hass.async_block_till_done()

    house = _house(entry, "Home")
    assert dict(house.data) == {
        "latitude": 50.0,
        "longitude": 8.0,
        "grid_meter": "sensor.grid_meter",
        "grid_meter_sign": SIGN_EXPORT,
        "inverters": [OMA, BUERO4],
        "battery_backed": [BUERO4],
    }
    assert house.unique_id
    assert "home" not in house.unique_id.lower()


async def test_duplicate_name_refused(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A second House with an existing name gets a form error."""
    entry = await setup_entry(
        hass, dtu_network, SimDtu.default(), houses=[SimHouse("Home")]
    )
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("home"))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"name": "name_exists"}
    result = await _next(hass, result, _first("Cabin"))
    assert result["step_id"] == "inverters"


async def test_house_without_inverters_skips_battery_step(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Choosing no Inverter creates the House straight away."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _create(hass, entry, "Empty", [], [])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _house(entry, "Empty").data
    assert data["inverters"] == []
    assert data["battery_backed"] == []


async def test_assigned_inverter_not_offered_to_another_house(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """An Inverter of one House is not offered to the next, and a forgery fails."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", inverters=[OMA])],
    )
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Cabin"))
    assert OMA not in _offered(result)
    assert sorted(_offered(result)) == sorted([BUERO4, BUERO5])
    with pytest.raises(InvalidData):
        await _next(hass, result, {"inverters": [OMA]})


async def test_inverter_taken_meanwhile_is_refused(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Two flows open at once cannot both take the same Inverter."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    first = await _start(hass, entry)
    first = await _next(hass, first, _first("A"))
    second = await _start(hass, entry)
    second = await _next(hass, second, _first("B"))

    first = await _next(hass, first, {"inverters": [OMA]})
    await _next(hass, first, {"battery_backed": []})
    await hass.async_block_till_done()

    result = await _next(hass, second, {"inverters": [OMA]})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "inverters"
    assert result["errors"] == {"base": "inverter_assigned"}
    assert _house(entry, "A").data["inverters"] == [OMA]


async def test_inverters_of_two_dtus_in_one_house(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Inverters from several DTUs can be assigned to the same House."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default(), second_dtu())
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Home"))
    assert {OMA, GARAGE_1, GARAGE_2} <= set(_offered(result))
    assert _labels(result)[GARAGE_1] == "Garage 1 (OpenDTU-Garage, HM-600-4T)"
    result = await _next(hass, result, {"inverters": [OMA, GARAGE_1]})
    result = await _next(hass, result, {"battery_backed": [GARAGE_1]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert data["inverters"] == [OMA, GARAGE_1]
    assert data["battery_backed"] == [GARAGE_1]


async def test_reconfigure_changes_everything(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Name, Grid Meter, sign and assignment can all be changed later."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        second_dtu(),
        houses=[
            SimHouse("Home", inverters=[OMA, BUERO4], battery_backed=[BUERO4]),
            SimHouse("Other", inverters=[BUERO5]),
        ],
    )
    house = _house(entry, "Home")
    unique_id = house.unique_id
    result = await _start(hass, entry, house)
    assert result["step_id"] == "reconfigure"
    defaults = _defaults(result)
    assert defaults["name"] == "Home"
    assert defaults["grid_meter"] == "sensor.grid_meter"

    # The name of another House is still refused, the own name is fine.
    clash = await _next(hass, result, _first("Other"))
    assert clash["errors"] == {"name": "name_exists"}

    result = await _next(hass, clash, _first("Villa", "sensor.new_meter", SIGN_EXPORT))
    assert result["step_id"] == "inverters"
    assert BUERO5 not in _offered(result)
    assert {OMA, BUERO4, GARAGE_1} <= set(_offered(result))
    defaults = _defaults(result)
    assert defaults["inverters"] == [OMA, BUERO4]
    result = await _next(hass, result, {"inverters": [BUERO4, GARAGE_2]})
    defaults = _defaults(result)
    assert defaults["battery_backed"] == [BUERO4]
    result = await _next(hass, result, {"battery_backed": [GARAGE_2]})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    changed = _house(entry, "Villa")
    assert changed.subentry_id == house.subentry_id
    assert changed.unique_id == unique_id
    assert dict(changed.data) == {
        "latitude": 50.0,
        "longitude": 8.0,
        "grid_meter": "sensor.new_meter",
        "grid_meter_sign": SIGN_EXPORT,
        "inverters": [BUERO4, GARAGE_2],
        "battery_backed": [GARAGE_2],
    }
    # The Inverter released by this House is now free for others.
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Third"))
    assert OMA in _offered(result)


async def test_unknown_assigned_inverter_stays_assigned(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """An assigned Inverter nobody reports right now stays selected."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", inverters=[OMA, "999000111222"])],
    )
    result = await _start(hass, entry, _house(entry, "Home"))
    result = await _next(hass, result, _first("Home"))
    assert "999000111222" in _offered(result)
    defaults = _defaults(result)
    assert defaults["inverters"] == [OMA, "999000111222"]
    result = await _next(hass, result, {"inverters": defaults["inverters"]})
    await _next(hass, result, {"battery_backed": []})
    await hass.async_block_till_done()
    assert list(_house(entry, "Home").data["inverters"]) == [OMA, "999000111222"]


async def test_inverters_of_a_down_dtu_are_still_offered(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """After a restart with the DTU down, its Inverters come from the registry."""
    dtu = SimDtu.default()
    entry = await setup_entry(
        hass, dtu_network, dtu, houses=[SimHouse("Home", inverters=[OMA])]
    )
    dtu.down = True
    dtu_network.apply()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    result = await _start(hass, entry, _house(entry, "Home"))
    result = await _next(hass, result, _first("Home"))
    labels = _labels(result)
    assert sorted(labels) == sorted([OMA, BUERO4, BUERO5])
    assert labels[OMA] == "OmaOpa (OpenDTU-Buero, HM-600-4T)"
    result = await _next(hass, result, {"inverters": [OMA]})
    result = await _next(hass, result, {"battery_backed": []})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert list(_house(entry, "Home").data["inverters"]) == [OMA]
