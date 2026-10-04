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

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .dtu_models import InverterSnapshot
from .entity import (
    DtuEntity,
    InverterEntity,
    setup_dtu_entities,
    setup_inverter_entities,
)


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
