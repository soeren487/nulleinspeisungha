"""Tests for the House module's computations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.nulleinspeisung.const import SIGN_EXPORT, SIGN_IMPORT
from custom_components.nulleinspeisung.dtu_models import DtuSnapshot, InverterSnapshot
from custom_components.nulleinspeisung.house import (
    House,
    HouseConfig,
    grid_power_from_state,
    sum_known,
)


def _state(value: str, unit: str | None = "W") -> State:
    """A Grid Meter state."""
    attributes = {"unit_of_measurement": unit} if unit else {}
    return State("sensor.grid_meter", value, attributes)


@pytest.mark.parametrize(
    ("state", "sign", "expected"),
    [
        (_state("250"), SIGN_IMPORT, 250.0),
        (_state("250"), SIGN_EXPORT, -250.0),
        (_state("-80.5"), SIGN_IMPORT, -80.5),
        (_state("-80.5"), SIGN_EXPORT, 80.5),
        (_state("1.5", "kW"), SIGN_IMPORT, 1500.0),
        (_state("1.5", "kW"), SIGN_EXPORT, -1500.0),
        (_state("12", None), SIGN_IMPORT, 12.0),
        (_state("0"), SIGN_EXPORT, 0.0),
        (_state("unknown"), SIGN_IMPORT, None),
        (_state("unavailable"), SIGN_EXPORT, None),
        (_state("abc"), SIGN_IMPORT, None),
        (_state("nan"), SIGN_IMPORT, None),
        (_state("5", "V"), SIGN_IMPORT, None),
        (None, SIGN_IMPORT, None),
    ],
)
def test_grid_power_from_state(
    state: State | None, sign: str, expected: float | None
) -> None:
    """Grid Power is in W, import positive, or unknown."""
    result = grid_power_from_state(state, sign)
    assert result == expected
    if expected == 0.0:
        assert str(result) == "0.0"


def test_sum_known() -> None:
    """Unknown values contribute nothing; no known value gives None."""
    assert sum_known([1.0, None, 2.5]) == 3.5
    assert sum_known([None, None]) is None
    assert sum_known([]) is None
    assert sum_known([0.0]) == 0.0


def _inverter(serial: str, power: float | None) -> InverterSnapshot:
    """An Inverter snapshot with the given power."""
    return InverterSnapshot(
        serial=serial,
        name=serial,
        reachable=True,
        producing=True,
        poll_enabled=True,
        data_age=1,
        power=power,
        limit=100.0,
        limit_set_status="Ok",
        rated_power=600,
        model=None,
    )


def _dtu(powers: dict[str, float | None], ok: bool = True) -> Any:
    """A stand-in for a DTU coordinator."""
    snapshot = DtuSnapshot(
        hostname="h",
        firmware_version="v",
        chip_model="c",
        uptime=1,
        inverters={s: _inverter(s, p) for s, p in powers.items()},
    )
    return SimpleNamespace(data=snapshot, last_update_success=ok)


def _house(
    hass: HomeAssistant, dtus: dict[str, Any], inverters: list[str], bb: list[str]
) -> House:
    """A House over the given stand-in DTUs."""
    config = HouseConfig(
        subentry_id="s",
        unique_id="u",
        name="Home",
        latitude=0.0,
        longitude=0.0,
        grid_meter="sensor.grid_meter",
        grid_meter_sign=SIGN_IMPORT,
        inverters=tuple(inverters),
        battery_backed=tuple(bb),
    )
    return House(hass, config, dtus)


def test_production_split_and_sum(hass: HomeAssistant) -> None:
    """PV and Battery-backed production add up to the total, across DTUs."""
    dtus = {
        "a": _dtu({"1": 100.0, "2": 50.0}),
        "b": _dtu({"3": 25.0, "4": 999.0}),
    }
    house = _house(hass, dtus, ["1", "2", "3"], ["3"])
    assert house.inverter_production() == 175.0
    assert house.pv_production() == 150.0
    assert house.battery_backed_production() == 25.0
    assert house.inverter_count == 3


def test_down_dtu_and_unknown_power_contribute_nothing(hass: HomeAssistant) -> None:
    """A down DTU or an unknown power drops only that Inverter."""
    dtus = {"a": _dtu({"1": 100.0, "2": None}), "b": _dtu({"3": 25.0}, ok=False)}
    house = _house(hass, dtus, ["1", "2", "3"], ["3"])
    assert house.inverter_production() == 100.0
    assert house.pv_production() == 100.0
    assert house.battery_backed_production() is None


def test_production_unavailable_without_known_power(hass: HomeAssistant) -> None:
    """No assigned Inverter with a known power makes production unknown."""
    house = _house(hass, {"a": _dtu({"1": None})}, ["1", "9"], [])
    assert house.inverter_production() is None
    empty = _house(hass, {}, [], [])
    assert empty.inverter_production() is None
    assert empty.inverter_count == 0


def test_battery_backed_outside_assignment_is_ignored() -> None:
    """A Battery-backed serial that is not assigned does not count."""
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigSubentry

    subentry = ConfigSubentry(
        data=MappingProxyType(
            {
                "latitude": 1,
                "longitude": 2,
                "grid_meter": "sensor.x",
                "inverters": ["1"],
                "battery_backed": ["1", "2"],
            }
        ),
        subentry_type="house",
        title="H",
        unique_id="u",
    )
    config = HouseConfig.from_subentry(subentry)
    assert config.battery_backed == ("1",)
    assert config.pv_inverters == ()
    assert config.grid_meter_sign == SIGN_IMPORT
