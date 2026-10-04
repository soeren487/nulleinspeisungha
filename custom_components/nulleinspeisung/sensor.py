"""Sensors of an Inverter."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .curtailment import ControlState
from .dtu_models import InverterSnapshot
from .entity import (
    DtuEntity,
    HouseEntity,
    InverterEntity,
    setup_dtu_entities,
    setup_house_entities,
    setup_inverter_entities,
)
from .house import House


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
        key="inverter_count",
        translation_key="inverter_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda house: house.inverter_count,
        always_available=True,
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
        entry,
        async_add_entities,
        lambda house: [
            *(HouseSensor(house, description) for description in HOUSE_DESCRIPTIONS),
            ControlStateSensor(house),
            InverterLimitSensor(house),
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
        elif not description.always_available:
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


class _ControlSensor(HouseEntity, SensorEntity):
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
        super().__init__(house, "control_state")

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
        super().__init__(house, "inverter_limit")

    @property
    def native_value(self) -> int | None:
        """Percent asked of the group; unknown while Curtailment is off."""
        control = self.house.control
        return control.requested_percent if control.curtailment else None
