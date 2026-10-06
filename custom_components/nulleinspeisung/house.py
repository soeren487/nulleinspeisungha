"""A House: its configuration and the values computed from it.

The values here are only computed; the control loop is ``HouseControl``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr

from .battery_gateway import BatteryGateway, BatteryState
from .battery_overrides import OverrideRelease, WantedOverrides
from .battery_priority import battery_headroom
from .battery_watch import BatteryWatch
from .const import (
    CONF_BATTERY_BACKED,
    CONF_BATTERY_CAPACITY,
    CONF_GRID_METER,
    CONF_GRID_METER_SIGN,
    CONF_GRID_METER_TOPIC,
    CONF_GX_HOST,
    CONF_GX_PORT,
    CONF_GX_PORTAL_ID,
    CONF_INVERTERS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_TIBBER_HOME,
    DEFAULT_GX_PORT,
    DOMAIN,
    INVERTER_MAKER,
    SIGN_EXPORT,
    SIGN_IMPORT,
    SUBENTRY_TYPE_HOUSE,
)
from .dtu_client import DtuClient
from .dtu_models import DtuSnapshot
from .expected_load import ExpectedLoad
from .grid_publisher import GridPublisher
from .house_control import HouseControl
from .house_prices import HousePrices

if TYPE_CHECKING:
    from .forecast import HouseForecast

_WATTS_PER_UNIT = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0, "mW": 0.001}


class DtuSource(Protocol):
    """What a House needs from a DTU's coordinator."""

    client: DtuClient
    data: DtuSnapshot | None
    last_update_success: bool
    restart_generation: int
    """Changes whenever the DTU restarted."""

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
    tibber_home: str | None = None
    gx_host: str | None = None
    """Address of the AC Battery's GX; ``None`` when the House has no AC Battery."""
    gx_port: int = DEFAULT_GX_PORT
    gx_portal_id: str | None = None
    battery_capacity: float | None = None
    """Usable capacity of the AC Battery in kWh."""
    grid_meter_topic: str | None = None
    """Topic to publish Grid Power to; only meaningful with an AC Battery."""

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
            tibber_home=data.get(CONF_TIBBER_HOME) or None,
            gx_host=data.get(CONF_GX_HOST) or None,
            gx_port=int(data.get(CONF_GX_PORT, DEFAULT_GX_PORT)),
            gx_portal_id=data.get(CONF_GX_PORTAL_ID) or None,
            battery_capacity=data.get(CONF_BATTERY_CAPACITY),
            grid_meter_topic=data.get(CONF_GRID_METER_TOPIC) or None,
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
        prices: HousePrices | None = None,
        entry: ConfigEntry | None = None,
        forecast: HouseForecast | None = None,
        gateway: BatteryGateway | None = None,
    ) -> None:
        """Create the House on top of the coordinators of the entry's DTUs."""
        self.hass = hass
        self.config = config
        self.dtus = dtus
        self.entry = entry
        """The config entry, for alerts; without it no repair issue is raised."""
        self.prices = prices
        """The House's Tibber prices; ``None`` without a Tibber home and token.

        Later features read ``prices.current`` and ``prices.future`` from here.
        """
        self.gateway = gateway
        """The House's AC Battery; ``None`` without one."""
        self.control = HouseControl(self)
        self.battery_watch = BatteryWatch(self)
        self.wanted_overrides = WantedOverrides()
        """The GX overrides the integration wants; later tickets fill it in."""
        self.override_release = OverrideRelease(self)
        self.grid_publisher: GridPublisher | None = (
            GridPublisher(self, config.grid_meter_topic)
            if gateway is not None and config.grid_meter_topic
            else None
        )
        """Publishes Grid Power to the AC Battery; ``None`` without battery or topic."""
        self.forecast = forecast
        """The House's PV Forecast; ``None`` without PV Inverters.

        Later features read ``forecast.future`` and ``forecast.usable`` from here.
        """
        if forecast is not None:
            forecast.attach(self)
        self.expected_load = ExpectedLoad(hass, self)
        """The House's Expected Load; every House has one.

        Later features read ``expected_load.at(start)`` and
        ``expected_load.upcoming()`` from here.
        """

    @property
    def battery(self) -> BatteryState | None:
        """The AC Battery's state; ``None`` when the House has none."""
        return self.gateway.state if self.gateway is not None else None

    def battery_headroom_raw(self) -> float:
        """Charge power in W the AC Battery could still take, whatever happened."""
        state = self.battery
        if state is None:
            return 0.0
        return battery_headroom(
            state.charge_level,
            state.power,
            state.voltage,
            state.charge_current_limit,
            self.control.maximum_charge_power,
            state.fresh,
        )

    def battery_headroom(self) -> float:
        """The headroom Curtailment may count on right now, in W."""
        if self.control.headroom_suppressed():
            return 0.0
        return self.battery_headroom_raw()

    def consumption(self) -> float | None:
        """The House's consumption in W, ``None`` when it cannot be told.

        Grid Power plus the production of the Inverters minus the power going
        into the AC Battery.
        """
        grid = self.grid_power()
        if grid is None:
            return None
        battery_power = 0.0
        if self.gateway is not None:
            state = self.gateway.state
            if not state.fresh or state.power is None:
                return None
            battery_power = state.power
        return grid + (self.inverter_production() or 0.0) - battery_power

    @property
    def inverter_count(self) -> int:
        """Number of Inverters assigned to the House."""
        return len(self.config.inverters)

    def grid_power(self) -> float | None:
        """Grid Power in W with import positive, or ``None`` if unknown."""
        return grid_power_from_state(
            self.hass.states.get(self.config.grid_meter), self.config.grid_meter_sign
        )

    def grid_meter_last_reported(self) -> float | None:
        """When the Grid Meter's sensor last reported, as a timestamp.

        Home Assistant updates this even when the value is unchanged.
        """
        state = self.hass.states.get(self.config.grid_meter)
        return state.last_reported_timestamp if state is not None else None

    def dtus_answering(self) -> bool | None:
        """Whether any DTU of the House's Inverters answers.

        ``None`` when the House has no Inverter or no DTU is known to hold any.
        """
        if not self.config.inverters:
            return None
        relevant = [
            dtu
            for dtu in self.dtus.values()
            if dtu.data is None
            or any(s in dtu.data.inverters for s in self.config.inverters)
        ]
        if not relevant:
            return None
        return any(dtu.last_update_success for dtu in relevant)

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

    def pv_production_complete(self) -> float | None:
        """Production of the PV Inverters, only if every one of them is known."""
        powers = [self.inverter_power(s) for s in self.config.pv_inverters]
        if not powers or any(p is None for p in powers):
            return None
        return sum(p for p in powers if p is not None)

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
        if device.via_device_id is None or device.manufacturer != INVERTER_MAKER:
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
