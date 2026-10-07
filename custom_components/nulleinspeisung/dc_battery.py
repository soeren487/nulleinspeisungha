"""The DC Batteries behind a House's Battery-backed Inverters.

Pure functions, no Home Assistant but the ``State`` of a sensor; the time is
passed in. Three things: the energy stored (read from the sensors the owner
named), the usual output of an Inverter per quarter-hour of the local day
(learned from its own history), and the DC Battery Support, the energy the
Inverters are expected to deliver until a deadline, which the AC Battery then
does not have to deliver.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State

from .expected_load_model import SLOTS_PER_DAY, slot_of
from .quarter_recorder import QuarterRecord
from .sunrise_energy import slots

CONVERSION_FACTOR = 0.85
"""Share of the energy stored in a DC Battery that comes out of its Inverter as AC
energy. An assumption, not a measurement."""
USUAL_PERCENTILE = 0.8
"""The usual output is this percentile of a quarter-hour's recorded values, so that
a few nights with empty DC Batteries do not pull a fixed output down."""
MIN_DAYS = 3
"""Days a quarter-hour of the day needs to have been recorded for its usual output
to count."""

_KWH_PER_UNIT = {"Wh": 0.001, "kWh": 1.0}
_ENERGY_CLASSES = frozenset({"energy", "energy_storage"})


@dataclass(frozen=True, slots=True)
class StoredEnergy:
    """The energy stored according to some sensors."""

    kwh: float
    """Sum of the readable sensors; the others count 0."""
    readable: int
    """Sensors that gave a value."""
    configured: int
    """Sensors the owner named."""


def sensor_kwh(state: State | None) -> float | None:
    """The energy a sensor reports, in kWh, or ``None`` when it gives none.

    The unit decides (Wh, kWh). Any other unit, or none, counts as Wh if the
    device class is an energy class and otherwise makes the sensor useless.
    Missing, unknown, unavailable, not numeric and not finite values give
    ``None``; a negative value counts as 0.
    """
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    unit = state.attributes.get("unit_of_measurement")
    factor = _KWH_PER_UNIT.get(unit) if isinstance(unit, str) else None
    if factor is None:
        if state.attributes.get("device_class") not in _ENERGY_CLASSES:
            return None
        factor = _KWH_PER_UNIT["Wh"]
    return max(value * factor, 0.0)


def stored_energy(
    entity_ids: Iterable[str], get_state: Callable[[str], State | None]
) -> StoredEnergy:
    """The energy stored behind some sensors; ``get_state`` reads one sensor."""
    total = 0.0
    readable = configured = 0
    for entity_id in entity_ids:
        configured += 1
        kwh = sensor_kwh(get_state(entity_id))
        if kwh is not None:
            readable += 1
            total += kwh
    return StoredEnergy(total, readable, configured)


def stored_energy_by_inverter(
    sensors: Mapping[str, Sequence[str]], get_state: Callable[[str], State | None]
) -> dict[str, StoredEnergy]:
    """The energy stored behind each Inverter that has sensors."""
    return {
        serial: stored_energy(entity_ids, get_state)
        for serial, entity_ids in sensors.items()
        if entity_ids
    }


def house_stored_energy(per_inverter: Mapping[str, StoredEnergy]) -> StoredEnergy:
    """The energy stored behind all of a House's Inverters."""
    return StoredEnergy(
        sum(e.kwh for e in per_inverter.values()),
        sum(e.readable for e in per_inverter.values()),
        sum(e.configured for e in per_inverter.values()),
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    """The percentile with linear interpolation between neighbouring values."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def usual_output(records: Iterable[QuarterRecord], tz: tzinfo) -> dict[int, float]:
    """The usual output in W of one Inverter, per quarter-hour of the local day.

    The key is the slot of the day (0 to 95, local time), the value the 80th
    percentile of the recorded values of that slot. A slot recorded on fewer than
    ``MIN_DAYS`` days is 0.
    """
    by_slot: dict[int, list[float]] = {}
    for record in records:
        by_slot.setdefault(slot_of(record.start, tz), []).append(record.watts)
    return {
        slot: _percentile(values, USUAL_PERCENTILE) if len(values) >= MIN_DAYS else 0.0
        for slot, values in by_slot.items()
    } | {slot: 0.0 for slot in range(SLOTS_PER_DAY) if slot not in by_slot}


@dataclass(frozen=True, slots=True)
class DcSupport:
    """The DC Battery Support until a deadline."""

    watts: Mapping[datetime, float]
    """Mean W of support per quarter-hour start; only quarter-hours with support."""
    kwh: float
    """The support in all, in kWh."""


def dc_battery_support(
    now: datetime,
    deadline: datetime,
    outputs: Mapping[str, Mapping[int, float]],
    stored_kwh: Mapping[str, float],
    tz: tzinfo,
    factor: float = CONVERSION_FACTOR,
) -> DcSupport:
    """What the Battery-backed Inverters deliver from ``now`` to ``deadline``.

    Each Inverter delivers its usual output (``outputs``: slot of the local day to
    W) in every quarter-hour, the current one with its remaining fraction, until
    the energy delivered reaches its stored energy (``stored_kwh``) times
    ``factor``; the last quarter-hour may be partial. An Inverter without stored
    energy gives no support. The W of a quarter-hour are the mean over the part of
    it before the deadline; the Inverters' are summed.
    """
    watts: dict[datetime, float] = {}
    total = 0.0
    for serial, usual in outputs.items():
        budget = max(stored_kwh.get(serial, 0.0), 0.0) * factor
        for slot, hours in slots(now, deadline):
            if budget <= 0.0:
                break
            wanted = max(usual.get(slot_of(slot, tz), 0.0), 0.0) * hours / 1000.0
            if wanted <= 0.0:
                continue
            delivered = min(wanted, budget)
            budget -= delivered
            total += delivered
            watts[slot] = watts.get(slot, 0.0) + delivered * 1000.0 / hours
    return DcSupport(watts, total)
