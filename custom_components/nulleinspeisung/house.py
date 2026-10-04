"""A House: its configuration and the values computed from it.

This is the seed of the House coordinator. It only knows and computes; it
controls nothing.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BATTERY_BACKED,
    CONF_GRID_METER,
    CONF_GRID_METER_SIGN,
    CONF_INVERTERS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    DOMAIN,
    SIGN_EXPORT,
    SIGN_IMPORT,
    SUBENTRY_TYPE_HOUSE,
)
from .dtu_models import DtuSnapshot

_WATTS_PER_UNIT = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0, "mW": 0.001}


class DtuSource(Protocol):
    """What a House needs from a DTU's coordinator."""

    data: DtuSnapshot | None
    last_update_success: bool

    def async_add_listener(
        self, update_callback: Callable[[], None], context: Any = None
    ) -> Callable[[], None]:
        """Call ``update_callback`` on every update; returns the remover."""


@dataclass(frozen=True, slots=True)
class HouseConfig:
    """What the owner configured for one House."""

    subentry_id: str
    unique_id: str
    name: str
    latitude: float
    longitude: float
    grid_meter: str
    grid_meter_sign: str
    inverters: tuple[str, ...]
    battery_backed: tuple[str, ...]

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> HouseConfig:
        """Read a House from its config subentry."""
        data = subentry.data
        inverters = tuple(data.get(CONF_INVERTERS, ()))
        return cls(
            subentry_id=subentry.subentry_id,
            unique_id=subentry.unique_id or subentry.subentry_id,
            name=subentry.title,
            latitude=float(data[CONF_LATITUDE]),
            longitude=float(data[CONF_LONGITUDE]),
            grid_meter=data[CONF_GRID_METER],
            grid_meter_sign=data.get(CONF_GRID_METER_SIGN, SIGN_IMPORT),
            inverters=inverters,
            battery_backed=tuple(
                s for s in data.get(CONF_BATTERY_BACKED, ()) if s in inverters
            ),
        )

    @property
    def pv_inverters(self) -> tuple[str, ...]:
        """The assigned Inverters that are fed directly by panels."""
        return tuple(s for s in self.inverters if s not in self.battery_backed)


def grid_power_from_state(state: State | None, sign: str) -> float | None:
    """Grid Power in W, import positive, from the Grid Meter's state.

    ``None`` when the sensor is missing, unknown, unavailable, not numeric or
    reports in a unit that is not a power.
    """
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement") or "W"
    factor = _WATTS_PER_UNIT.get(unit)
    if factor is None or not math.isfinite(value):
        return None
    watts = value * factor
    if sign == SIGN_EXPORT:
        watts = -watts
    return watts + 0.0  # turns -0.0 into 0.0


def sum_known(powers: Iterable[float | None]) -> float | None:
    """Sum of the known powers; ``None`` when none is known."""
    known = [p for p in powers if p is not None]
    return sum(known) if known else None


class House:
    """One House: configuration plus the values derived from the live data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: HouseConfig,
        dtus: Mapping[str, DtuSource],
    ) -> None:
        """Create the House on top of the coordinators of the entry's DTUs."""
        self.hass = hass
        self.config = config
        self.dtus = dtus

    @property
    def inverter_count(self) -> int:
        """Number of Inverters assigned to the House."""
        return len(self.config.inverters)

    def grid_power(self) -> float | None:
        """Grid Power in W with import positive, or ``None`` if unknown."""
        return grid_power_from_state(
            self.hass.states.get(self.config.grid_meter), self.config.grid_meter_sign
        )

    def inverter_power(self, serial: str) -> float | None:
        """AC power of one Inverter, ``None`` if its DTU is down or it is unknown."""
        for dtu in self.dtus.values():
            if not dtu.last_update_success or dtu.data is None:
                continue
            inverter = dtu.data.inverters.get(serial)
            if inverter is not None:
                return inverter.power
        return None

    def production(self, serials: Iterable[str]) -> float | None:
        """Summed AC power of the given Inverters, ``None`` if none is known."""
        return sum_known(self.inverter_power(s) for s in serials)

    def inverter_production(self) -> float | None:
        """Production of all assigned Inverters in W."""
        return self.production(self.config.inverters)

    def pv_production(self) -> float | None:
        """Production of the assigned PV Inverters in W."""
        return self.production(self.config.pv_inverters)

    def battery_backed_production(self) -> float | None:
        """Production of the assigned Battery-backed Inverters in W."""
        return self.production(self.config.battery_backed)


def house_subentries(entry: ConfigEntry) -> list[ConfigSubentry]:
    """All House subentries of the entry."""
    return [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_HOUSE
    ]


def inverters_of_other_houses(
    entry: ConfigEntry, own_subentry_id: str | None
) -> set[str]:
    """Serials assigned to any House except the one being edited."""
    return {
        serial
        for subentry in house_subentries(entry)
        if subentry.subentry_id != own_subentry_id
        for serial in subentry.data.get(CONF_INVERTERS, ())
    }


def _label(name: str, hostname: str | None, model: str | None) -> str:
    """Describe an Inverter to the owner."""
    details = ", ".join(part for part in (hostname, model) if part)
    return f"{name} ({details})" if details else name


def known_inverters(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Every Inverter known to any DTU of the entry, serial to display label.

    Taken from the DTU coordinators' latest snapshots, plus Inverter devices in
    the device registry for DTUs that are currently down.
    """
    labels: dict[str, str] = {}
    runtime = getattr(entry, "runtime_data", None)
    dtus: Mapping[str, Any] = runtime.dtus if runtime is not None else {}
    for coordinator in dtus.values():
        snapshot = coordinator.data
        if snapshot is None:
            continue
        for serial, inverter in snapshot.inverters.items():
            labels[serial] = _label(inverter.name, snapshot.hostname, inverter.model)

    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.via_device_id is None:
            continue
        via = registry.async_get(device.via_device_id)
        for domain, serial in device.identifiers:
            if domain != DOMAIN or serial in labels:
                continue
            labels[serial] = _label(
                device.name_by_user or device.name or serial,
                via.name if via else None,
                device.model,
            )
    return labels
