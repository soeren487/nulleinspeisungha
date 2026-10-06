"""Sensors of an Inverter."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .curtailment import ControlState
from .dtu_models import InverterSnapshot
from .entity import (
    ChargingEntity,
    ControlEntity,
    DtuEntity,
    HouseEntity,
    InverterEntity,
    setup_dtu_entities,
    setup_house_entities,
    setup_inverter_entities,
)
from .forecast import slot_start
from .grid_charging import GridChargingState
from .house import House
from .price_source import PriceLevel

MAX_LISTED_SLOTS = 96
"""Planned slots listed in an attribute; a day has 96 quarter-hours."""


@dataclass(frozen=True, kw_only=True)
class InverterSensorDescription(SensorEntityDescription):
    """Describes a sensor read from the Inverter snapshot."""

    value_fn: Callable[[InverterSnapshot], float | None]


DESCRIPTIONS: tuple[InverterSensorDescription, ...] = (
    InverterSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda inverter: inverter.power,
    ),
    InverterSensorDescription(
        key="limit",
        translation_key="limit",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda inverter: inverter.limit,
    ),
    InverterSensorDescription(
        key="data_age",
        translation_key="data_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda inverter: inverter.data_age,
    ),
    InverterSensorDescription(
        key="rated_power",
        translation_key="rated_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda inverter: inverter.rated_power,
    ),
)


@dataclass(frozen=True, kw_only=True)
class HouseSensorDescription(SensorEntityDescription):
    """Describes a sensor computed by a House."""

    value_fn: Callable[[House], float | int | None]
    follows_grid_meter: bool = False
    """Update on the Grid Meter's state changes instead of on DTU updates."""
    always_available: bool = False
    follows_dtus: bool = False
    follows_battery: bool = False
    """Also update when the AC Battery's state changes."""


_POWER = {
    "device_class": SensorDeviceClass.POWER,
    "state_class": SensorStateClass.MEASUREMENT,
    "native_unit_of_measurement": UnitOfPower.WATT,
}

HOUSE_DESCRIPTIONS: tuple[HouseSensorDescription, ...] = (
    HouseSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        value_fn=lambda house: house.grid_power(),
        follows_grid_meter=True,
        **_POWER,
    ),
    HouseSensorDescription(
        key="inverter_production",
        translation_key="inverter_production",
        value_fn=lambda house: house.inverter_production(),
        **_POWER,
    ),
    HouseSensorDescription(
        key="pv_production",
        translation_key="pv_production",
        value_fn=lambda house: house.pv_production(),
        **_POWER,
    ),
    HouseSensorDescription(
        key="battery_backed_production",
        translation_key="battery_backed_production",
        value_fn=lambda house: house.battery_backed_production(),
        **_POWER,
    ),
    HouseSensorDescription(
        key="consumption",
        translation_key="consumption",
        value_fn=lambda house: house.consumption(),
        follows_grid_meter=True,
        follows_dtus=True,
        follows_battery=True,
        **_POWER,
    ),
    HouseSensorDescription(
        key="inverter_count",
        translation_key="inverter_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda house: house.inverter_count,
        always_available=True,
    ),
)


BATTERY_DESCRIPTIONS: tuple[HouseSensorDescription, ...] = (
    HouseSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda house: house.battery.charge_level if house.battery else None,
    ),
    HouseSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        value_fn=lambda house: house.battery.power if house.battery else None,
        **_POWER,
    ),
    HouseSensorDescription(
        key="battery_headroom",
        translation_key="battery_headroom",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda house: house.battery_headroom(),
        **_POWER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of all Inverters."""
    setup_inverter_entities(
        entry,
        async_add_entities,
        lambda coordinator, serial: [
            InverterSensor(coordinator, serial, description)
            for description in DESCRIPTIONS
        ],
    )
    setup_dtu_entities(
        entry,
        async_add_entities,
        lambda c: [DtuRestartCountSensor(c), DtuLastRestartSensor(c)],
    )
    setup_house_entities(
        hass,
        entry,
        async_add_entities,
        lambda house: [
            *(HouseSensor(house, description) for description in HOUSE_DESCRIPTIONS),
            ControlStateSensor(house),
            InverterLimitSensor(house),
            *([BatteryBackedLimitSensor(house)] if house.config.battery_backed else []),
            *(
                [BatterySensor(house, d) for d in BATTERY_DESCRIPTIONS]
                if house.gateway is not None
                else []
            ),
            *(
                [
                    PriceSensor(house),
                    PriceLevelSensor(house),
                    PricesKnownUntilSensor(house),
                ]
                if house.prices is not None
                else []
            ),
            *(
                [ReportedGridPowerSensor(house)]
                if house.grid_publisher is not None
                else []
            ),
            *(
                [
                    GridChargingStateSensor(house),
                    NextChargingStartSensor(house),
                    ChargingEnergySensor(house, "energy_to_buy"),
                    ChargingEnergySensor(house, "energy_missing"),
                    ReferencePriceSensor(house),
                    SunriseEnergySensor(house, "energy_needed_at_sunrise"),
                    SunriseEnergySensor(house, "battery_at_sunrise"),
                    SunriseEnergySensor(house, "forecast_surplus"),
                ]
                if house.grid_charging is not None
                else []
            ),
            ExpectedLoadSensor(house),
            ExpectedLoadEnergySensor(house),
            LoadHistoryDaysSensor(house),
            *(
                [
                    ForecastPowerSensor(house),
                    ForecastEnergySensor(house, "pv_forecast_today", 0),
                    ForecastEnergySensor(house, "pv_forecast_tomorrow", 1),
                    ForecastHistoryDaysSensor(house),
                ]
                if house.forecast is not None
                else []
            ),
        ],
    )


class InverterSensor(InverterEntity, SensorEntity):
    """A measured or reported number of an Inverter."""

    entity_description: InverterSensorDescription

    def __init__(
        self,
        coordinator: DtuCoordinator,
        serial: str,
        description: InverterSensorDescription,
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """The current value."""
        return self.entity_description.value_fn(self.inverter)


class DtuRestartCountSensor(DtuEntity, RestoreSensor):
    """How often the integration restarted the DTU."""

    _attr_translation_key = "restart_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DtuCoordinator) -> None:
        """Create the counter."""
        super().__init__(coordinator, "restart_count")

    async def async_added_to_hass(self) -> None:
        """Take over the count from before Home Assistant was restarted."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            with suppress(TypeError, ValueError):
                self.coordinator.supervisor.restart_count = int(last.native_value)

    @property
    def available(self) -> bool:
        """The count is known even while the DTU does not answer."""
        return True

    @property
    def native_value(self) -> int:
        """Restarts sent so far."""
        return self.coordinator.supervisor.restart_count


class DtuLastRestartSensor(DtuEntity, RestoreSensor):
    """When the integration last restarted the DTU."""

    _attr_translation_key = "last_restart"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DtuCoordinator) -> None:
        """Create the timestamp sensor."""
        super().__init__(coordinator, "last_restart")

    async def async_added_to_hass(self) -> None:
        """Take over the time from before Home Assistant was restarted."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, datetime):
            self.coordinator.supervisor.last_restart = last.native_value

    @property
    def available(self) -> bool:
        """The time is known even while the DTU does not answer."""
        return True

    @property
    def native_value(self) -> datetime | None:
        """When the last restart was sent."""
        return self.coordinator.supervisor.last_restart


class HouseSensor(HouseEntity, SensorEntity):
    """A value computed from the Grid Meter and the Inverters of a House."""

    entity_description: HouseSensorDescription

    def __init__(self, house: House, description: HouseSensorDescription) -> None:
        """Create the sensor."""
        super().__init__(house, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Follow the Grid Meter or the DTUs, whichever the value depends on."""
        await super().async_added_to_hass()
        description = self.entity_description
        if description.follows_grid_meter:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.house.config.grid_meter], self._handle_change
                )
            )
        if description.follows_battery and self.house.gateway is not None:
            self.async_on_remove(
                self.house.gateway.async_add_listener(self._handle_change)
            )
        if description.follows_dtus or not (
            description.follows_grid_meter or description.always_available
        ):
            for coordinator in self.house.dtus.values():
                self.async_on_remove(
                    coordinator.async_add_listener(self._handle_change)
                )

    @property
    def native_value(self) -> float | int | None:
        """The current value."""
        return self.entity_description.value_fn(self.house)

    @property
    def available(self) -> bool:
        """Whether the value is known."""
        return self.entity_description.always_available or self.native_value is not None


class BatterySensor(HouseEntity, SensorEntity):
    """A value read from the House's AC Battery, unavailable while it is silent."""

    entity_description: HouseSensorDescription

    def __init__(self, house: House, description: HouseSensorDescription) -> None:
        """Create the sensor."""
        super().__init__(house, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the battery or the control changes."""
        await super().async_added_to_hass()
        assert self.house.gateway is not None
        self.async_on_remove(self.house.gateway.async_add_listener(self._handle_change))
        self.async_on_remove(self.house.control.async_add_listener(self._handle_change))

    @property
    def native_value(self) -> float | None:
        """The current value."""
        return self.entity_description.value_fn(self.house)

    @property
    def available(self) -> bool:
        """Whether the AC Battery delivers and the value is known."""
        state = self.house.battery
        return state is not None and state.fresh and self.native_value is not None


class ReportedGridPowerSensor(HouseEntity, SensorEntity):
    """The Grid Power last published to the AC Battery."""

    _attr_translation_key = "reported_grid_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "reported_grid_power")
        assert house.grid_publisher is not None
        self._publisher = house.grid_publisher

    async def async_added_to_hass(self) -> None:
        """Write the state whenever something was published."""
        await super().async_added_to_hass()
        self.async_on_remove(self._publisher.async_add_listener(self._handle_change))

    @property
    def native_value(self) -> float | None:
        """The last value published; unknown while off or before any."""
        return self._publisher.last_published if self._publisher.enabled else None


class _ControlSensor(ControlEntity, SensorEntity):
    """A sensor that follows the House's control loop."""

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the control changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self.house.control.async_add_listener(self._handle_change))


class ControlStateSensor(_ControlSensor):
    """What the House's control is doing."""

    _attr_translation_key = "control_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = tuple(state.value for state in ControlState)  # type: ignore[assignment]

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "control_state", "sensor")

    @property
    def native_value(self) -> str:
        """The control state."""
        return self.house.control.state.value


class InverterLimitSensor(_ControlSensor):
    """The Inverter Limit the House currently asks of its Inverters."""

    _attr_translation_key = "inverter_limit"
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "inverter_limit", "sensor")

    @property
    def native_value(self) -> int | None:
        """Percent asked of the group; unknown while Curtailment is off."""
        control = self.house.control
        return control.requested_percent if control.curtailment else None


class BatteryBackedLimitSensor(_ControlSensor):
    """The Inverter Limit the House currently asks of its Battery-backed group."""

    _attr_translation_key = "battery_backed_limit"
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(
            house, "battery_backed_limit", "sensor", "battery_backed_inverter_limit"
        )

    @property
    def native_value(self) -> int | None:
        """Percent asked of the group; unknown while Curtailment is off."""
        control = self.house.control
        if not control.curtailment:
            return None
        return control.requested_percent_battery_backed


class _PriceSensor(HouseEntity, SensorEntity):
    """A sensor that follows the House's stored Tibber prices."""

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the prices or the current price change."""
        await super().async_added_to_hass()
        assert self.house.prices is not None
        self.async_on_remove(self.house.prices.async_add_listener(self._handle_change))


class PriceSensor(_PriceSensor):
    """The price per kWh valid right now."""

    _attr_translation_key = "price"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "price")

    @property
    def native_value(self) -> float | None:
        """Total price per kWh, unknown while no price covers the instant."""
        prices = self.house.prices
        return prices.current.total if prices and prices.current else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Currency per kWh."""
        currency = self.house.prices.currency if self.house.prices else None
        return f"{currency}/kWh" if currency else None


class PriceLevelSensor(_PriceSensor):
    """Tibber's Price Level of the quarter-hour now."""

    _attr_translation_key = "price_level"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = tuple(level.value for level in PriceLevel)  # type: ignore[assignment]

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "price_level")

    @property
    def native_value(self) -> str | None:
        """The Price Level, unknown while no price covers the instant."""
        prices = self.house.prices
        return prices.current.level.value if prices and prices.current else None


class PricesKnownUntilSensor(_PriceSensor):
    """The end of the last known price slot."""

    _attr_translation_key = "prices_known_until"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "prices_known_until")

    @property
    def native_value(self) -> datetime | None:
        """Where the known prices end."""
        return self.house.prices.known_until if self.house.prices else None


class _ForecastSensor(HouseEntity, SensorEntity):
    """A sensor that follows the House's PV Forecast."""

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the forecast or the history change."""
        await super().async_added_to_hass()
        assert self.house.forecast is not None
        self.async_on_remove(
            self.house.forecast.async_add_listener(self._handle_change)
        )


class ForecastPowerSensor(_ForecastSensor):
    """The PV Forecast of the current quarter-hour."""

    _attr_translation_key = "pv_forecast_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "pv_forecast_power")

    @property
    def native_value(self) -> float | None:
        """Expected production in W; unknown without a learned factor."""
        current = self.house.forecast.current if self.house.forecast else None
        return round(current.watts, 1) if current else None


class ForecastEnergySensor(_ForecastSensor):
    """The PV Forecast summed over a local day."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, house: House, key: str, days_ahead: int) -> None:
        """Create the sensor for today (0) or a later day."""
        super().__init__(house, key)
        self._attr_translation_key = key
        self._days_ahead = days_ahead

    @property
    def native_value(self) -> float | None:
        """Expected production of the day in kWh, unknown without a factor."""
        if self.house.forecast is None:
            return None
        day = dt_util.now().date() + timedelta(days=self._days_ahead)
        energy = self.house.forecast.energy_kwh(day)
        return round(energy, 3) if energy is not None else None


class ForecastHistoryDaysSensor(_ForecastSensor):
    """How many days of history count towards the forecast being usable."""

    _attr_translation_key = "pv_forecast_history_days"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "pv_forecast_history_days")

    @property
    def native_value(self) -> int | None:
        """Days with enough usable records."""
        return self.house.forecast.history_days if self.house.forecast else None


class _ExpectedLoadSensor(HouseEntity, SensorEntity):
    """A sensor that follows the House's Expected Load."""

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the Expected Load changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.house.expected_load.async_add_listener(self._handle_change)
        )


class ExpectedLoadSensor(_ExpectedLoadSensor):
    """The Expected Load of the current quarter-hour."""

    _attr_translation_key = "expected_load"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "expected_load")

    @property
    def native_value(self) -> float:
        """Expected consumption in W."""
        return round(self.house.expected_load.at(slot_start(dt_util.utcnow())), 1)


class ExpectedLoadEnergySensor(_ExpectedLoadSensor):
    """The Expected Load summed over the next 24 hours."""

    _attr_translation_key = "expected_load_24h"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "expected_load_24h")

    @property
    def native_value(self) -> float:
        """Expected consumption in kWh."""
        return round(self.house.expected_load.energy_next_24h(), 3)


class LoadHistoryDaysSensor(_ExpectedLoadSensor):
    """How many days of history count towards the Expected Load being learned."""

    _attr_translation_key = "load_history_days"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "load_history_days")

    @property
    def native_value(self) -> int:
        """Number of days with enough records."""
        return self.house.expected_load.learned_days


class GridChargingStateSensor(ChargingEntity, SensorEntity):
    """What Grid Charging is doing."""

    _attr_translation_key = "grid_charging_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = tuple(state.value for state in GridChargingState)  # type: ignore[assignment]

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "grid_charging_state", "sensor")

    @property
    def native_value(self) -> str:
        """The state of Grid Charging."""
        return self.charging.state.value


class NextChargingStartSensor(ChargingEntity, SensorEntity):
    """When the next planned quarter-hour starts, or the running one started."""

    _attr_translation_key = "next_charging_start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "next_charging_start", "sensor")

    @property
    def native_value(self) -> datetime | None:
        """Start of the slot being charged in or the next planned one."""
        return self.charging.next_start()

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """The planned slots as ISO start times."""
        plan = self.charging.plan
        slots = plan.slots[:MAX_LISTED_SLOTS] if plan is not None else ()
        return {"slots": [slot.start.isoformat() for slot in slots]}


class ChargingEnergySensor(ChargingEntity, SensorEntity):
    """An energy of the Charging Plan in kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, house: House, key: str) -> None:
        """Create the sensor ``energy_to_buy`` or ``energy_missing``."""
        super().__init__(house, key, "sensor")
        self._attr_translation_key = key
        self._key = key

    @property
    def native_value(self) -> float | None:
        """The energy, unknown without a plan."""
        plan = self.charging.plan
        if plan is None:
            return None
        return (
            plan.energy_to_buy if self._key == "energy_to_buy" else plan.energy_missing
        )


class ReferencePriceSensor(ChargingEntity, SensorEntity):
    """The price the energy bought by Grid Charging is expected to replace."""

    _attr_translation_key = "reference_price"
    _attr_suggested_display_precision = 4

    def __init__(self, house: House) -> None:
        """Create the sensor."""
        super().__init__(house, "reference_price", "sensor")

    @property
    def native_value(self) -> float | None:
        """The Reference Price, unknown when none could be formed."""
        plan = self.charging.plan
        return plan.reference_price if plan is not None else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Currency per kWh."""
        prices = self.house.prices
        currency = prices.currency if prices else None
        return f"{currency}/kWh" if currency else None


class SunriseEnergySensor(ChargingEntity, SensorEntity):
    """An energy worked out from the PV Forecast and the Expected Load, in kWh."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, house: House, key: str) -> None:
        """Create the sensor ``energy_needed_at_sunrise``, ``battery_at_sunrise`` or
        ``forecast_surplus``.
        """
        super().__init__(house, key, "sensor")
        self._attr_translation_key = key
        self._key = key

    @property
    def native_value(self) -> float | None:
        """The energy, unknown while it cannot be computed."""
        charging = self.charging
        if self._key == "energy_needed_at_sunrise":
            return charging.energy_needed
        if self._key == "battery_at_sunrise":
            return charging.battery_at_sunrise
        return charging.forecast_surplus
