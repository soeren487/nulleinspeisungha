"""The Nulleinspeisung integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PASSWORD, CONF_URL, SUBENTRY_TYPE_DTU
from .coordinator import DtuCoordinator
from .devices import DeviceSynchroniser
from .dtu_client import DtuClient
from .house import House, HouseConfig, house_subentries
from .house_knowledge import HouseKnowledge

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class NulleinspeisungData:
    """Runtime data of the entry."""

    dtus: dict[str, DtuCoordinator] = field(default_factory=dict)
    """The coordinator of every DTU, keyed by subentry id."""
    houses: dict[str, House] = field(default_factory=dict)
    """Every House, keyed by subentry id."""


type NulleinspeisungConfigEntry = ConfigEntry[NulleinspeisungData]


async def async_setup_entry(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> bool:
    """Set up Nulleinspeisung from a config entry."""
    session = async_get_clientsession(hass)
    coordinators: dict[str, DtuCoordinator] = {}
    knowledge = HouseKnowledge(
        [HouseConfig.from_subentry(s) for s in house_subentries(entry)], coordinators
    )
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_DTU:
            continue
        client = DtuClient(
            session, subentry.data[CONF_URL], subentry.data[CONF_PASSWORD]
        )
        coordinator = DtuCoordinator(hass, entry, subentry, client, knowledge)
        # A DTU that does not answer must not block the others: refresh without
        # raising, and let the listeners do their work on the first success.
        await coordinator.async_refresh()
        synchroniser = DeviceSynchroniser(hass, entry, coordinator)
        synchroniser()
        entry.async_on_unload(coordinator.async_add_listener(synchroniser))
        coordinators[subentry_id] = coordinator
    houses = {
        subentry.subentry_id: House(
            hass, HouseConfig.from_subentry(subentry), coordinators
        )
        for subentry in house_subentries(entry)
    }
    entry.runtime_data = NulleinspeisungData(dtus=coordinators, houses=houses)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The entities have restored the owner's settings; only now may the loop run.
    for house in houses.values():
        entry.async_on_unload(house.control.stop)
        house.control.start()
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_change(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> None:
    """Reload when a DTU or House is added, changed or removed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
