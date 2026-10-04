"""What the Houses know that is relevant to judging a DTU.

Answers two questions for the DTU supervisors without exposing House
internals: is an Inverter Battery-backed, and is a PV Inverter of one of the
same Houses producing on another DTU.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from .dtu_models import DtuSnapshot
from .house import HouseConfig

MIN_PRODUCING_POWER = 50.0
"""W a PV Inverter must deliver for its DTU to count as producing.

Roofs wake up at different times; a few watts at dawn say nothing.
"""


class DtuView(Protocol):
    """What the knowledge needs from a DTU's coordinator."""

    data: DtuSnapshot | None
    last_update_success: bool
    staleness_time: float
    """Seconds after which this DTU's Inverter data counts as no longer fresh."""


class HouseKnowledge:
    """House configurations seen from the DTUs' side."""

    def __init__(
        self, houses: Iterable[HouseConfig], dtus: Mapping[str, DtuView]
    ) -> None:
        """Create the knowledge from House configurations and live DTUs.

        ``dtus`` may still be filling up; it is only read when asked.
        """
        self._houses = tuple(houses)
        self._dtus = dtus
        self._battery_backed = frozenset(
            serial for house in self._houses for serial in house.battery_backed
        )

    def is_battery_backed(self, serial: str) -> bool:
        """Whether any House marks the Inverter as Battery-backed."""
        return serial in self._battery_backed

    def other_dtu_producing(self, dtu_key: str, own_serials: Iterable[str]) -> bool:
        """Whether a House of this DTU has a producing PV Inverter elsewhere.

        ``own_serials`` are the Inverters on the DTU ``dtu_key`` itself.
        """
        own = set(own_serials)
        for house in self._houses:
            if own.isdisjoint(house.inverters):
                continue
            for serial in house.inverters:
                if serial in own or self.is_battery_backed(serial):
                    continue
                if self._producing_elsewhere(dtu_key, serial):
                    return True
        return False

    def _producing_elsewhere(self, dtu_key: str, serial: str) -> bool:
        for key, dtu in self._dtus.items():
            if key == dtu_key or not dtu.last_update_success or dtu.data is None:
                continue
            inverter = dtu.data.inverters.get(serial)
            if inverter is None:
                continue
            return (
                inverter.producing
                and inverter.data_age < dtu.staleness_time
                and inverter.power is not None
                and inverter.power >= MIN_PRODUCING_POWER
            )
        return False
