"""The limit split: from a group's allowed production to a percent per Inverter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Protocol


class LimitSplit(Protocol):
    """A strategy that shares a group's allowed production among its Inverters."""

    def __call__(
        self,
        allowed_power: float,
        rated_powers: Mapping[str, float],
        floor_percent: float,
    ) -> dict[str, int]:
        """Whole-percent Inverter Limit per serial, from rated power in W."""


def split_equally(
    allowed_power: float,
    rated_powers: Mapping[str, float],
    floor_percent: float,
) -> dict[str, int]:
    """Give every Inverter the same whole percent, between the floor and 100."""
    total = sum(rated_powers.values())
    if total <= 0:
        return {}
    percent = math.floor(allowed_power / total * 100 + 0.5)
    percent = min(max(percent, math.ceil(floor_percent)), 100)
    return dict.fromkeys(rated_powers, percent)
