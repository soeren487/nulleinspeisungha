"""Base entity for everything that belongs to one Inverter."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HOUSE_MODEL
from .coordinator import DtuCoordinator
from .dtu_models import InverterSnapshot
from .house import House


def dtu_device_info(coordinator: DtuCoordinator) -> DeviceInfo:
    """Device description of the DTU itself."""
    snapshot = coordinator.data
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.dtu_serial)},
        name=snapshot.hostname,
        manufacturer="OpenDTU",
        model=snapshot.chip_model,
        sw_version=snapshot.firmware_version,
        configuration_url=coordinator.client.base_url,
    )


class InverterEntity(CoordinatorEntity[DtuCoordinator]):
    """An entity of one Inverter, available while its DTU answers."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DtuCoordinator, serial: str, key: str) -> None:
        """Create the entity ``key`` of the Inverter with this serial."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        inverter = coordinator.data.inverters[serial]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            via_device=(DOMAIN, coordinator.dtu_serial),
            name=inverter.name,
            manufacturer="Hoymiles",
            model=inverter.model,
            serial_number=serial,
        )

    @property
    def inverter(self) -> InverterSnapshot:
        """The Inverter's latest snapshot."""
        return self.coordinator.data.inverters[self._serial]

    @property
    def available(self) -> bool:
        """Whether the DTU answered and still lists the Inverter."""
        data = self.coordinator.data
        return super().available and data is not None and self._serial in data.inverters


def setup_inverter_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[DtuCoordinator, str], list[Entity]],
) -> None:
    """Add entities for every Inverter of every DTU, including later arrivals."""
    for subentry_id, coordinator in entry.runtime_data.dtus.items():
        known: set[str] = set()

        def add_new(
            coordinator: DtuCoordinator = coordinator,
            subentry_id: str = subentry_id,
            known: set[str] = known,
        ) -> None:
            if coordinator.data is None:
                return
            new = [s for s in coordinator.data.inverters if s not in known]
            known.update(new)
            entities = [e for serial in new for e in build(coordinator, serial)]
            async_add_entities(entities, config_subentry_id=subentry_id)

        add_new()
        entry.async_on_unload(coordinator.async_add_listener(add_new))


class DtuEntity(CoordinatorEntity[DtuCoordinator]):
    """An entity of the DTU itself, shown on the DTU's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DtuCoordinator, key: str) -> None:
        """Create the entity ``key`` of this DTU."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.dtu_serial}_{key}"
        self._attr_device_info = dtu_device_info(coordinator)


def setup_dtu_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[DtuCoordinator], list[Entity]],
) -> None:
    """Add the entities of every DTU as soon as its device exists."""
    for subentry_id, coordinator in entry.runtime_data.dtus.items():
        added = False

        def add_once(
            coordinator: DtuCoordinator = coordinator,
            subentry_id: str = subentry_id,
        ) -> None:
            nonlocal added
            if added or coordinator.data is None:
                return
            added = True
            async_add_entities(build(coordinator), config_subentry_id=subentry_id)

        add_once()
        entry.async_on_unload(coordinator.async_add_listener(add_once))


class HouseEntity(Entity):
    """An entity of a House, shown on the House's device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, house: House, key: str) -> None:
        """Create the entity ``key`` of this House."""
        self.house = house
        self._attr_unique_id = f"{house.config.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, house.config.unique_id)},
            name=house.config.name,
            manufacturer="Nulleinspeisung",
            model=HOUSE_MODEL,
        )

    @callback
    def _handle_change(self, *_: object) -> None:
        """Write the new state after an input of the House changed."""
        self.async_write_ha_state()


def setup_house_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[House], list[Entity]],
) -> None:
    """Add the entities of every House to the House's subentry."""
    for subentry_id, house in entry.runtime_data.houses.items():
        async_add_entities(build(house), config_subentry_id=subentry_id)
