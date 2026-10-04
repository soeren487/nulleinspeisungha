"""Base entity for everything that belongs to one Inverter."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DtuCoordinator
from .dtu_models import InverterSnapshot


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
    for subentry_id, coordinator in entry.runtime_data.items():
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
