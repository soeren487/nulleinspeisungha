"""Diagnostics of the config entry: what the integration knows, nothing that
identifies the installation.

Addresses, host names, passwords, tokens, portal ids, coordinates, topics, home
ids and unique ids are redacted; the serials of DTUs and Inverters are replaced
by stable placeholders (``dtu-1``, ``inverter-1``), numbered in the order of their
sorted serials, so that the same serial has the same placeholder everywhere.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NulleinspeisungConfigEntry
from .const import (
    CONF_BATTERY_BACKED,
    CONF_DC_BATTERIES,
    CONF_GRID_METER_TOPIC,
    CONF_GX_HOST,
    CONF_GX_PORTAL_ID,
    CONF_INVERTERS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAC,
    CONF_NOTIFY_TARGET,
    CONF_PASSWORD,
    CONF_TIBBER_HOME,
    CONF_TIBBER_TOKEN,
    CONF_URL,
    SUBENTRY_TYPE_DTU,
)
from .house import House

TO_REDACT = {
    CONF_URL,
    CONF_PASSWORD,
    CONF_MAC,
    CONF_TIBBER_TOKEN,
    CONF_TIBBER_HOME,
    CONF_NOTIFY_TARGET,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_GX_HOST,
    CONF_GX_PORTAL_ID,
    CONF_GRID_METER_TOPIC,
    "location",
    "unique_id",
}
"""Keys whose values are replaced wherever they occur."""
REDACTED = "**REDACTED**"


class _Placeholders:
    """Stable placeholders for serials."""

    def __init__(self, dtus: set[str], inverters: set[str]) -> None:
        self._names = {
            **{s: f"dtu-{i}" for i, s in enumerate(sorted(dtus), 1)},
            **{s: f"inverter-{i}" for i, s in enumerate(sorted(inverters), 1)},
        }

    def __call__(self, serial: str) -> str:
        return self._names.get(serial, REDACTED)


def _plain(value: Any) -> Any:
    """``value`` as JSON-able data: dataclasses as dicts, times as ISO strings."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v) for v in value]
    return value


def _subentry_data(data: dict[str, Any], placeholder: _Placeholders) -> dict[str, Any]:
    """The data of a subentry with identifying values replaced."""
    out = dict(async_redact_data(data, TO_REDACT))
    for key in (CONF_INVERTERS, CONF_BATTERY_BACKED):
        if key in out:
            out[key] = [placeholder(s) for s in out[key]]
    if CONF_DC_BATTERIES in out:
        out[CONF_DC_BATTERIES] = {
            placeholder(serial): list(entity_ids)
            for serial, entity_ids in (data.get(CONF_DC_BATTERIES) or {}).items()
        }
    return out


def _house(house: House, placeholder: _Placeholders) -> dict[str, Any]:
    config = house.config
    result: dict[str, Any] = {
        "name": config.name,
        "settings": {
            "grid_meter_sign": config.grid_meter_sign,
            "inverters": [placeholder(s) for s in config.inverters],
            "battery_backed": [placeholder(s) for s in config.battery_backed],
            "battery_capacity": config.battery_capacity,
            "has_prices": house.prices is not None,
            "has_forecast": house.forecast is not None,
            "has_battery": house.gateway is not None,
            "publishes_grid_power": house.grid_publisher is not None,
        },
        "battery": _plain(house.battery) if house.battery is not None else None,
        "wanted_overrides": _plain(house.wanted_overrides),
    }
    charging = house.grid_charging
    if charging is not None:
        result["grid_charging"] = {
            "settings": {
                "enabled": charging.enabled,
                "ignore_efficiency": charging.ignore_efficiency,
                "charge_target": charging.charge_target,
                "battery_efficiency": charging.battery_efficiency,
                "price_levels": charging.price_levels,
                "use_forecast": charging.use_forecast,
                "forecast_share": charging.forecast_share,
                "safety_buffer": charging.safety_buffer,
                "top_up": charging.top_up,
                "top_up_level": charging.top_up_level,
            },
            "state": charging.state.value,
            "top_up_wanted": charging.top_up_wanted,
            "forecast_in_use": charging.forecast_in_use,
            "inputs": [_plain(quarter) for quarter in charging.inputs],
            "plan": _plain(charging.plan),
            "reason": _plain(charging.reason()),
        }
    return result


def _dtu(coordinator: Any, placeholder: _Placeholders) -> dict[str, Any]:
    snapshot = coordinator.data
    supervisor = coordinator.supervisor
    return {
        "last_update_success": coordinator.last_update_success,
        "restart_generation": coordinator.restart_generation,
        "restart_count": supervisor.restart_count,
        "last_restart": _plain(supervisor.last_restart),
        "inverters": [
            {
                "serial": placeholder(serial),
                "name": inverter.name,
                "reachable": inverter.reachable,
                "limit": inverter.limit,
                "power": inverter.power,
            }
            for serial, inverter in (snapshot.inverters.items() if snapshot else ())
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> dict[str, Any]:
    """Diagnostics of the entry."""
    runtime = entry.runtime_data
    dtu_serials = {
        s.unique_id
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_TYPE_DTU and s.unique_id
    }
    inverter_serials: set[str] = set()
    for coordinator in runtime.dtus.values():
        if coordinator.data is not None:
            inverter_serials.update(coordinator.data.inverters)
    for subentry in entry.subentries.values():
        inverter_serials.update(subentry.data.get(CONF_INVERTERS, ()))
        inverter_serials.update(subentry.data.get(CONF_BATTERY_BACKED, ()))
        inverter_serials.update((subentry.data.get(CONF_DC_BATTERIES) or {}).keys())
    placeholder = _Placeholders(dtu_serials, inverter_serials)
    dtu_names = {
        subentry_id: placeholder(sub.unique_id or "")
        for subentry_id, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_DTU
    }
    return {
        "entry": {
            "title": REDACTED,
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
            "subentries": [
                {
                    "type": sub.subentry_type,
                    "title": dtu_names.get(subentry_id, sub.title),
                    "data": _subentry_data(dict(sub.data), placeholder),
                }
                for subentry_id, sub in entry.subentries.items()
            ],
        },
        "houses": [_house(house, placeholder) for house in runtime.houses.values()],
        "dtus": [
            {"id": dtu_names.get(subentry_id, REDACTED)}
            | _dtu(coordinator, placeholder)
            for subentry_id, coordinator in runtime.dtus.items()
        ],
    }
