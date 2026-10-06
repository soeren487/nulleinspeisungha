"""Base entity for everything that belongs to one Inverter."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import CHARGING_MODEL, CONTROL_MODEL, DOMAIN, HOUSE_MODEL, INVERTER_MAKER
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
            manufacturer=INVERTER_MAKER,
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


def house_device_info(house: House) -> DeviceInfo:
    """Device description of the House itself."""
    return DeviceInfo(
        identifiers={(DOMAIN, house.config.unique_id)},
        name=house.config.name,
        manufacturer="Nulleinspeisung",
        model=HOUSE_MODEL,
    )


def control_device_info(house: House) -> DeviceInfo:
    """Device description of the inverter control of the House, below the House."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{house.config.unique_id}_control")},
        via_device=(DOMAIN, house.config.unique_id),
        translation_key="inverter_control",
        translation_placeholders={"house": house.config.name},
        manufacturer="Nulleinspeisung",
        model=CONTROL_MODEL,
    )


def charging_device_info(house: House) -> DeviceInfo:
    """Device description of the Grid Charging of the House, below the House."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{house.config.unique_id}_charging")},
        via_device=(DOMAIN, house.config.unique_id),
        translation_key="grid_charging",
        translation_placeholders={"house": house.config.name},
        manufacturer="Nulleinspeisung",
        model=CHARGING_MODEL,
    )


class HouseEntity(Entity):
    """An entity of a House, shown on the House's device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, house: House, key: str) -> None:
        """Create the entity ``key`` of this House."""
        self.house = house
        self._attr_unique_id = f"{house.config.unique_id}_{key}"
        self._attr_device_info = house_device_info(house)

    def move_to_control_device(self, domain: str, object_id: str) -> None:
        """Show the entity on the control device, under its entity id of old.

        The device name would put "inverter control" into the entity id; the id
        is pinned to the House name and ``object_id`` instead, as it was when
        the entity still sat on the House device.
        """
        self._attr_device_info = control_device_info(self.house)
        self.entity_id = f"{domain}.{slugify(self.house.config.name)}_{object_id}"

    @callback
    def _handle_change(self, *_: object) -> None:
        """Write the new state after an input of the House changed."""
        self.async_write_ha_state()


class ControlEntity(HouseEntity):
    """An entity of the inverter control of a House, shown on its own device."""

    def __init__(
        self, house: House, key: str, domain: str, object_id: str | None = None
    ) -> None:
        """Create the entity ``key`` of the control of this House."""
        super().__init__(house, key)
        self.move_to_control_device(domain, object_id or key)


class ChargingEntity(HouseEntity):
    """An entity of the Grid Charging of a House, shown on its own device."""

    def __init__(self, house: House, key: str, domain: str) -> None:
        """Create the entity ``key`` of the Grid Charging of this House.

        The device name would put "grid charging" into the entity id; the id is
        pinned to the House name and the key instead.
        """
        super().__init__(house, key)
        assert house.grid_charging is not None
        self.charging = house.grid_charging
        self._attr_device_info = charging_device_info(house)
        self.entity_id = f"{domain}.{slugify(house.config.name)}_{key}"

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the plan or the state may have changed."""
        await super().async_added_to_hass()
        self.async_on_remove(self.charging.async_add_listener(self._handle_change))


def setup_house_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[House], list[Entity]],
) -> None:
    """Add the entities of every House to the House's subentry."""
    devices = dr.async_get(hass)
    for subentry_id, house in entry.runtime_data.houses.items():
        # The control device points at the House device, so that must exist
        # whichever platform is set up first.
        devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            **house_device_info(house),
        )
        async_add_entities(build(house), config_subentry_id=subentry_id)
