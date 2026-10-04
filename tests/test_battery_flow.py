"""The AC Battery step of the House flow."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nulleinspeisung import battery_gateway
from custom_components.nulleinspeisung.const import SUBENTRY_TYPE_HOUSE
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, register_gx, setup_entry
from tests.test_house_flow import (
    OMA,
    _defaults,
    _first,
    _house,
    _next,
    _start,
)


@pytest.fixture(autouse=True)
def short_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GX that does not answer fails after a moment instead of ten seconds."""
    monkeypatch.setattr(battery_gateway, "PROBE_TIMEOUT", 0.05)


async def _to_battery_step(
    hass: HomeAssistant, entry: ConfigEntry, name: str = "Home", inverters=()
) -> ConfigFlowResult:
    result = await _start(hass, entry)
    result = await _next(hass, result, _first(name))
    result = await _next(hass, result, {"inverters": list(inverters)})
    if inverters:
        result = await _next(hass, result, {"battery_backed": []})
    assert result["step_id"] == "ac_battery"
    return result


def _fields(result: ConfigFlowResult) -> list[str]:
    return [key.schema for key in result["data_schema"].schema]


async def test_battery_step_stores_host_port_portal_id_and_capacity(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A reachable GX is stored with the portal id it names."""
    gx = register_gx(SimGx("gx-home.test", 1884))
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _to_battery_step(hass, entry, inverters=[OMA])
    assert _fields(result) == ["gx_host", "gx_port", "battery_capacity"]
    assert _defaults(result)["gx_port"] == 1883
    result = await _next(
        hass,
        result,
        {"gx_host": " gx-home.test ", "gx_port": 1884, "battery_capacity": 9.6},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert data["gx_host"] == "gx-home.test"
    assert data["gx_port"] == 1884
    assert data["gx_portal_id"] == gx.portal_id
    assert data["battery_capacity"] == 9.6


async def test_battery_step_is_shown_for_a_house_without_inverters(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House that has only a battery can be created."""
    gx = register_gx(SimGx())
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _to_battery_step(hass, entry)
    result = await _next(hass, result, {"gx_host": gx.host, "gx_port": 1883})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert data["inverters"] == []
    assert data["gx_portal_id"] == gx.portal_id
    assert "battery_capacity" not in data


async def test_empty_host_means_no_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Without an address the House has no AC Battery and nothing is probed."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _to_battery_step(hass, entry)
    result = await _next(hass, result, {"gx_port": 1883, "battery_capacity": 5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert not {k for k in data if k.startswith("gx_") or k == "battery_capacity"}


async def test_unreachable_gx_is_a_form_error_and_can_be_corrected(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A GX that does not answer gives cannot_connect; a corrected address works."""
    gx = register_gx(SimGx())
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _to_battery_step(hass, entry)
    result = await _next(hass, result, {"gx_host": "typo.test", "gx_port": 1883})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ac_battery"
    assert result["errors"] == {"gx_host": "cannot_connect"}
    gx.down = True
    result = await _next(hass, result, {"gx_host": gx.host, "gx_port": 1883})
    assert result["errors"] == {"gx_host": "cannot_connect"}
    gx.down = False
    result = await _next(hass, result, {"gx_host": gx.host, "gx_port": 1883})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_gx_of_another_house_is_refused(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Two Houses cannot share one GX, even under another address."""
    first = SimGx("gx-first.test")
    alias = SimGx("gx-alias.test")
    alias.portal_id = first.portal_id
    register_gx(alias)
    entry = await setup_entry(
        hass, dtu_network, SimDtu.default(), houses=[SimHouse("First", gx=first)]
    )
    result = await _to_battery_step(hass, entry, "Second")
    result = await _next(hass, result, {"gx_host": "gx-alias.test", "gx_port": 1883})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"gx_host": "gx_assigned"}


async def _reconfigure(
    hass: HomeAssistant, entry: ConfigEntry, house: ConfigSubentry
) -> ConfigFlowResult:
    result = await _start(hass, entry, house)
    result = await _next(hass, result, _first("Home"))
    result = await _next(hass, result, {"inverters": []})
    assert result["step_id"] == "ac_battery"
    return result


async def test_reconfigure_prefills_and_keeps_the_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The stored GX is suggested; submitting it again keeps it, own GX allowed."""
    gx = SimGx("gx-home.test")
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", gx=gx, capacity=9.6)],
    )
    result = await _reconfigure(hass, entry, _house(entry, "Home"))
    suggestions = {
        key.schema: key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggestions == {"gx_host": "gx-home.test", "battery_capacity": 9.6}
    result = await _next(
        hass,
        result,
        {"gx_host": "gx-home.test", "gx_port": 1883, "battery_capacity": 12},
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert data["gx_portal_id"] == gx.portal_id
    assert data["battery_capacity"] == 12


async def test_reconfigure_can_remove_the_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Clearing the address removes every battery key from the House."""
    gx = SimGx("gx-home.test")
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", gx=gx, capacity=9.6)],
    )
    result = await _reconfigure(hass, entry, _house(entry, "Home"))
    result = await _next(hass, result, {"gx_port": 1883})
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    data = _house(entry, "Home").data
    assert not {k for k in data if k.startswith("gx_") or k == "battery_capacity"}
    assert any(
        s.subentry_type == SUBENTRY_TYPE_HOUSE for s in entry.subentries.values()
    )
