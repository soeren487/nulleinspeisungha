"""Immutable snapshots of a DTU and its Inverters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DtuIdentity:
    """Who a DTU is, as needed to add it."""

    serial: str
    hostname: str
    mac: str | None = None
    """Hardware address, lower case without separators; ``None`` if unknown."""


@dataclass(frozen=True, slots=True)
class InverterSnapshot:
    """One Inverter as last reported by its DTU."""

    serial: str
    name: str
    reachable: bool
    producing: bool
    poll_enabled: bool
    data_age: int
    """Seconds since the DTU last received data from the Inverter."""
    power: float | None
    """AC power in W, summed over all AC channels; ``None`` if not reported."""
    limit: float
    """Inverter Limit in percent of the rated power."""
    limit_set_status: str
    rated_power: int | None
    """Rated power in W, ``None`` while the DTU does not know it yet."""
    model: str | None
    """Hoymiles model name, ``None`` while the DTU does not know it yet."""


@dataclass(frozen=True, slots=True)
class DtuSnapshot:
    """A DTU and all its Inverters at one moment."""

    hostname: str
    firmware_version: str
    chip_model: str
    uptime: int
    inverters: dict[str, InverterSnapshot]
    """Inverters keyed by serial."""
