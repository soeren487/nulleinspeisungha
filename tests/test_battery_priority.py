"""The battery headroom and the Feed-in Setpoint clamp, tested directly."""

from __future__ import annotations

import pytest

from custom_components.nulleinspeisung.battery_priority import (
    battery_headroom,
    import_target,
)


def _headroom(**kwargs: float | bool | None) -> float:
    values = {
        "charge_level": 50.0,
        "power": 0.0,
        "voltage": None,
        "charge_current_limit": None,
        "maximum_charge_power": 2100.0,
        "fresh": True,
    } | kwargs
    return battery_headroom(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # Idle: the Maximum Charge Power less the margin of 100 W.
        ({}, 2000),
        # Charging takes from the headroom.
        ({"power": 500.0}, 1500),
        # Discharging counts as not charging at all.
        ({"power": -800.0}, 2000),
        # At the Maximum Charge Power, or beyond it, nothing is left.
        ({"power": 2100.0}, 0),
        ({"power": 3000.0}, 0),
        # Within the margin of the limit nothing is left either.
        ({"power": 2050.0}, 0),
        # The BMS limit (50 A at 50 V = 2500 W) does not bite above the setting.
        ({"voltage": 50.0, "charge_current_limit": 50.0}, 2000),
        # ... but bites below it (20 A at 50 V = 1000 W).
        ({"voltage": 50.0, "charge_current_limit": 20.0}, 900),
        ({"voltage": 50.0, "charge_current_limit": 20.0, "power": 700.0}, 200),
        # A BMS that allows no charge leaves no headroom.
        ({"voltage": 50.0, "charge_current_limit": 0.0}, 0),
        # Without voltage or without limit the BMS cannot be taken into account.
        ({"voltage": 50.0}, 2000),
        ({"charge_current_limit": 1.0}, 2000),
        # The setting is what the owner chose.
        ({"maximum_charge_power": 500.0}, 400),
        ({"maximum_charge_power": 100.0}, 0),
    ],
)
def test_headroom_of_a_battery_below_full(
    kwargs: dict[str, float], expected: float
) -> None:
    """The battery takes the smaller of setting and BMS limit, less charge, margin."""
    assert _headroom(**kwargs) == pytest.approx(expected)


@pytest.mark.parametrize("charge_level", [100.0, 100.5])
def test_full_battery_has_no_headroom(charge_level: float) -> None:
    """At 100 % or more the battery takes nothing."""
    assert _headroom(charge_level=charge_level) == 0


def test_stale_or_unknown_state_has_no_headroom() -> None:
    """Without a fresh state, or without charge level or power, nothing is counted."""
    assert _headroom(fresh=False) == 0
    assert _headroom(charge_level=None) == 0
    assert _headroom(power=None) == 0


@pytest.mark.parametrize(
    ("setpoint", "battery", "target", "capped"),
    [
        # Zero and positive Feed-in Setpoints are never changed.
        (0.0, 0.0, 0.0, False),
        (0.0, -50.0, 0.0, False),
        (300.0, 0.0, -300.0, False),
        (300.0, None, -300.0, False),
        # Without a known battery setpoint nothing is clamped.
        (-100.0, None, 100.0, False),
        # The example: asking for 100 W import with a battery setpoint of 0 W.
        (-100.0, 0.0, 0.0, True),
        # Less import than the battery setpoint is kept.
        (-100.0, 100.0, 100.0, False),
        (-100.0, 150.0, 100.0, False),
        (-100.0, 50.0, 50.0, True),
        # A battery that itself exports pulls the target below zero.
        (-100.0, -20.0, -20.0, True),
    ],
)
def test_target_never_asks_for_more_import_than_the_battery_setpoint(
    setpoint: float, battery: float | None, target: float, capped: bool
) -> None:
    """The target is the negated setpoint, capped by the battery's grid setpoint."""
    assert import_target(setpoint, battery) == (target, capped)
