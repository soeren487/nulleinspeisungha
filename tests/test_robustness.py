"""Small faults found in the review of 2026-11: stop, start-up, warnings, logs."""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, SimTibber, setup_entry
from tests.test_discharge_block_flow import (
    _blocking,
    _discharge,
    _on_gx,
)

OMA, BUERO4 = "114183178036", "116191100801"


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


async def test_the_overrides_set_are_released_when_home_assistant_stops(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The GX gets its discharge override back before the connection closes."""
    _, gx = await _blocking(hass, dtu_network, tibber, freezer)
    assert _on_gx(gx) == 0.0
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert _discharge(gx)[-1] == -1
    assert _on_gx(gx) is None


async def test_nothing_is_released_that_the_integration_did_not_set(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Another system's override stays when Home Assistant stops."""
    gx = SimGx()
    gx.set("hub4/0/Overrides/MaxDischargePower", 0)
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", gx=gx, grid_topic="grid/test/house")],
    )
    before = list(gx.other_publishes)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert gx.other_publishes == before


async def test_the_minimum_is_unknown_until_the_gx_reports_it(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """No 0 % for a moment: without a reported minimum the sensor has no value."""
    gx = SimGx()
    gx.no_minimum()
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", gx=gx)],
    )
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "house-home_battery_minimum_soc"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "unavailable"
    gx.set("settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit", 65.0)
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == 65.0


async def test_no_deprecation_warning_for_the_device_info(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    mqtt_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parent devices are given by id; ``via_device`` is deprecated."""
    caplog.set_level(logging.DEBUG)
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                gx=SimGx(),
                grid_topic="grid/test/house",
            )
        ],
    )
    assert "via_device" not in caplog.text
    assert "device_registry" not in caplog.text


async def test_a_dtu_that_stays_down_is_logged_once(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    caplog: pytest.LogCaptureFixture,
    freezer,
) -> None:
    """Failed polls log once when the outage starts and once on recovery."""
    dtu = SimDtu.default()
    await setup_entry(hass, dtu_network, dtu, houses=[SimHouse("Home")])
    caplog.clear()
    dtu.down = True
    dtu_network.apply()
    for _ in range(5):
        await _tick(hass, freezer, 60)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) <= 1
    dtu.down = False
    dtu_network.apply()
    await _tick(hass, freezer, 60)
    assert sum("Fetching" in r.getMessage() for r in errors) <= 1
