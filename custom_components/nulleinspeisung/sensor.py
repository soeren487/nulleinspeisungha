"""Sensors of an Inverter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
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
from .entity import InverterEntity, setup_inverter_entities


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
