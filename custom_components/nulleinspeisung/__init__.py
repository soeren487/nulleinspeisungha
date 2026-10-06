"""The Nulleinspeisung integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .battery_gateway import BatteryGateway
from .const import CONF_PASSWORD, CONF_TIBBER_TOKEN, CONF_URL, SUBENTRY_TYPE_DTU
from .coordinator import DtuCoordinator
from .devices import DeviceSynchroniser
from .dtu_client import DtuClient
from .forecast import HouseForecast
from .forecast_source import async_create_client as async_create_forecast_client
from .house import House, HouseConfig, house_subentries
from .house_knowledge import HouseKnowledge
from .house_prices import HousePrices
from .price_source import async_create_client

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
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
    token = entry.options.get(CONF_TIBBER_TOKEN)
    tibber = await async_create_client(hass, token) if token else None
    houses: dict[str, House] = {}
    forecast_client = None
    for subentry in house_subentries(entry):
        config = HouseConfig.from_subentry(subentry)
        prices = None
        if tibber is not None and config.tibber_home:
            prices = HousePrices(
                hass, entry, tibber, config.tibber_home, config.name, config.unique_id
            )
            # Tibber being down must not stop the entry from loading.
            await prices.async_start()
            entry.async_on_unload(prices.stop)
        forecast = None
        if config.pv_inverters:
            if forecast_client is None:
                forecast_client = await async_create_forecast_client(hass)
            forecast = HouseForecast(
                hass,
                entry,
                forecast_client,
                config.name,
                config.unique_id,
                config.latitude,
                config.longitude,
            )
        gateway = None
        if config.gx_host:
            gateway = BatteryGateway(
                hass, config.gx_host, config.gx_port, config.gx_portal_id
            )
            # A GX that does not answer must not stop the entry from loading.
            await gateway.async_start()
            entry.async_on_unload(gateway.async_stop)
        house = House(hass, config, coordinators, prices, entry, forecast, gateway)
        houses[subentry.subentry_id] = house
        if forecast is not None:
            # Open-Meteo being down must not stop the entry from loading.
            await forecast.async_start()
            entry.async_on_unload(forecast.async_stop)
        await house.expected_load.async_start()
        entry.async_on_unload(house.expected_load.async_stop)
    entry.runtime_data = NulleinspeisungData(dtus=coordinators, houses=houses)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The entities have restored the owner's settings; only now may the loop run.
    for house in houses.values():
        entry.async_on_unload(house.control.stop)
        house.control.start()
        entry.async_on_unload(house.battery_watch.stop)
        house.battery_watch.start()
        entry.async_on_unload(house.override_control.stop)
        house.override_control.start()
        if house.grid_charging is not None:
            entry.async_on_unload(house.grid_charging.stop)
            house.grid_charging.start()
        if house.grid_publisher is not None:
            entry.async_on_unload(house.grid_publisher.stop)
            house.grid_publisher.start()
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
