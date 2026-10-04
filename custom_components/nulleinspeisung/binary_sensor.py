"""Binary sensors of an Inverter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
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
class InverterBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor read from the Inverter snapshot."""

    value_fn: Callable[[InverterSnapshot], bool]


DESCRIPTIONS: tuple[InverterBinarySensorDescription, ...] = (
    InverterBinarySensorDescription(
        key="reachable",
        translation_key="reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda inverter: inverter.reachable,
    ),
    InverterBinarySensorDescription(
        key="producing",
        translation_key="producing",
        value_fn=lambda inverter: inverter.producing,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of all Inverters."""
    setup_inverter_entities(
        entry,
        async_add_entities,
        lambda coordinator, serial: [
            InverterBinarySensor(coordinator, serial, description)
            for description in DESCRIPTIONS
        ],
    )
    setup_dtu_entities(entry, async_add_entities, lambda c: [DtuStuckSensor(c)])


class InverterBinarySensor(InverterEntity, BinarySensorEntity):
    """A true/false property of an Inverter."""

    entity_description: InverterBinarySensorDescription

    def __init__(
        self,
        coordinator: DtuCoordinator,
        serial: str,
        description: InverterBinarySensorDescription,
    ) -> None:
        """Create the binary sensor."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Whether the property holds."""
        return self.entity_description.value_fn(self.inverter)


class DtuStuckSensor(DtuEntity, BinarySensorEntity):
    """Whether the DTU is a Stuck DTU right now."""

    _attr_translation_key = "stuck"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: DtuCoordinator) -> None:
        """Create the stuck indicator."""
        super().__init__(coordinator, "stuck")

    @property
    def is_on(self) -> bool | None:
        """Whether the DTU is stuck; unknown while it does not answer."""
        return self.coordinator.supervisor.stuck
