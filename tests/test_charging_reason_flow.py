"""The Grid charging reason and the plan as data, end to end.

The worlds are those of ``test_top_up_flow`` (24 October 2026, a raised minimum)
and ``test_discharge_block_flow`` (the night of 4 to 5 October).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nulleinspeisung.charging_reason import ReasonCode
from tests.conftest import DtuNetwork, SimTibber
from tests.test_discharge_block_flow import (
    ALL_DAY_CHEAP,
    TWO_CHEAP,
    _blocking,
)
from tests.test_grid_charging_flow import _call, _settle, _setup, _state, _value
from tests.test_top_up_flow import ON, _at_14, _gx, _start


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _reason(hass: HomeAssistant) -> str:
    return _value(hass, "sensor", "charging_reason")


def _text(hass: HomeAssistant) -> str:
    return _state(hass, "sensor", "charging_reason").attributes["text"]


async def test_the_options_are_the_codes(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    state = _state(hass, "sensor", "charging_reason")
    assert list(state.attributes["options"]) == [code.value for code in ReasonCode]
    assert state.attributes["device_class"] == "enum"


async def test_waiting_for_the_cheap_quarters_of_the_top_up(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    attributes = _state(hass, "sensor", "charging_reason").attributes
    assert _reason(hass) == "waiting"
    # The cheap quarters are 14:00 to 16:00 local; the latest ones are planned.
    assert attributes["text"] == "Waiting; next charging at 14:30."
    assert attributes["text_de"] == "Wartet; nächstes Laden um 14:30."
    assert attributes["next_start"] == "2026-10-24T12:30:00+00:00"
    assert attributes["power_w"] is None


async def test_charging_for_the_top_up_without_the_buffer(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)
    await _call(hass, "number", "set_value", "safety_buffer", value=0)
    await _settle(hass, freezer, gx, to="2026-10-24T14:45")

    attributes = _state(hass, "sensor", "charging_reason").attributes
    assert _reason(hass) == "charging_top_up"
    assert attributes["power_w"] == 2100
    assert attributes["text"] == "Charging at 2.1 kW for the BatteryLife top-up."
    assert attributes["text_de"] == "Lädt mit 2,1 kW für die BatteryLife-Vollladung."


async def test_charging_because_the_safety_buffer_applies(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _at_14(hass, dtu_network, tibber, freezer)

    attributes = _state(hass, "sensor", "charging_reason").attributes
    assert _reason(hass) == "charging_safety_buffer"
    assert attributes["power_w"] == pytest.approx(
        float(_value(hass, "sensor", "buffer_charge_power")), abs=1
    )
    assert attributes["text"].startswith("Charging at ")
    assert attributes["text"].endswith(
        "for the BatteryLife top-up because the Safety Buffer applies."
    )
    assert "Sicherheitspuffer" in attributes["text_de"]


async def test_holding_the_battery_for_the_dear_day(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """02:05 in a cheap quarter-hour with a full battery: held, nothing charged."""
    await _blocking(hass, dtu_network, tibber, freezer)

    attributes = _state(hass, "sensor", "charging_reason").attributes
    assert _reason(hass) == "holding"
    assert attributes["held_until"] == "2026-10-05T00:30:00+00:00"  # 02:30 local
    assert attributes["shortfall_at"] is not None
    assert attributes["shortfall_kwh"] > 0.0
    assert attributes["shortfall_price"] == pytest.approx(0.40)
    assert attributes["text"].startswith("Holding the battery until 02:30 for a ")
    assert attributes["text"].endswith("(40.0 ct).")
    assert attributes["text_de"].startswith("Hält die Batterie bis 02:30 für einen ")


async def test_nothing_to_do_when_every_quarter_is_cheap(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        level=100.0,
        prices=ALL_DAY_CHEAP,
    )

    attributes = _state(hass, "sensor", "charging_reason").attributes
    assert _reason(hass) == "idle"
    assert attributes["text"].startswith("Nothing to do: no shortfall until the")
    assert attributes["text_de"].startswith("Nichts zu tun")


async def test_off_and_on_again(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _blocking(hass, dtu_network, tibber, freezer, prices=TWO_CHEAP)
    assert _reason(hass) == "holding"

    await _call(hass, "switch", "turn_off", "grid_charging")
    assert _reason(hass) == "off"
    assert _text(hass) == "Grid charging is switched off."

    await _call(hass, "switch", "turn_on", "grid_charging")
    assert _reason(hass) == "holding"


async def test_the_plan_is_available_as_data(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _start(hass, dtu_network, tibber, freezer, _gx(), switches=ON)

    attributes = _state(hass, "sensor", "next_charging_start").attributes
    plan = attributes["plan"]
    assert [p["start"] for p in plan] == attributes["slots"]
    assert all(set(p) == {"start", "end", "grid_kwh", "top_up", "buffer"} for p in plan)
    assert all(p["top_up"] is True and p["buffer"] is False for p in plan)
    assert sum(p["grid_kwh"] for p in plan) == pytest.approx(
        float(_value(hass, "sensor", "energy_to_buy")), abs=0.01
    )
    assert not set(attributes["held"]) & set(attributes["slots"])


async def test_the_held_quarters_are_listed_where_nothing_is_charged(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _blocking(hass, dtu_network, tibber, freezer)

    attributes = _state(hass, "sensor", "next_charging_start").attributes
    assert attributes["slots"] == []
    assert attributes["plan"] == []
    assert attributes["held"][:2] == [
        "2026-10-05T00:00:00+00:00",
        "2026-10-05T00:15:00+00:00",
    ]
    assert attributes["held"] == sorted(attributes["held"])
    assert len(attributes["held"]) <= 192


async def test_the_plan_attributes_stay_out_of_the_recorder(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    from custom_components.nulleinspeisung.sensor import NextChargingStartSensor

    assert {"slots", "plan", "held"} <= NextChargingStartSensor._unrecorded_attributes
