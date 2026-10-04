"""Pure rules for giving the AC Battery priority over export.

No Home Assistant, no I/O. Powers are in W; Grid Power is positive for import.
"""

from __future__ import annotations

HEADROOM_MARGIN = 100.0
"""W kept free of the headroom, so the battery is not asked for its last watt."""


def battery_headroom(
    charge_level: float | None,
    power: float | None,
    voltage: float | None,
    charge_current_limit: float | None,
    maximum_charge_power: float,
    fresh: bool,
) -> float:
    """The extra charge power the AC Battery could take right now.

    The battery takes at most the Maximum Charge Power setting and, when
    known, what its BMS allows (charge current limit times voltage). From that
    the current charge power and a margin are taken off. Zero when the battery
    is full, when its state is not fresh, or when charge level or power are
    unknown.
    """
    if not fresh or charge_level is None or power is None:
        return 0.0
    if charge_level >= 100:
        return 0.0
    can_take = maximum_charge_power
    if charge_current_limit is not None and voltage is not None:
        can_take = min(can_take, charge_current_limit * voltage)
    charging = max(power, 0.0)
    return max(can_take - charging - HEADROOM_MARGIN, 0.0)


def import_target(
    feed_in_setpoint: float, battery_grid_setpoint: float | None
) -> tuple[float, bool]:
    """The Grid Power to aim for, and whether the battery's setpoint capped it.

    The target is the negated Feed-in Setpoint. A negative Feed-in Setpoint asks
    for import, which the AC Battery would regulate away: the target never
    exceeds the battery's stored grid setpoint. A Feed-in Setpoint of zero or
    more is never changed.
    """
    target = -feed_in_setpoint + 0.0
    if (
        feed_in_setpoint < 0
        and battery_grid_setpoint is not None
        and target > battery_grid_setpoint
    ):
        return battery_grid_setpoint, True
    return target, False
