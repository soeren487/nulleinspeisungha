"""Selects of a House: the owner's choice for the failure state."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NulleinspeisungConfigEntry
from .entity import ChargingEntity, ControlEntity, setup_house_entities
from .grid_charging import PRICE_LEVEL_OPTIONS
from .house import House
from .house_control import ON_FAILURE_OPTIONS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the failure choice of every House."""
    setup_house_entities(
        hass,
        entry,
        async_add_entities,
        lambda house: [
            OnFailureSelect(house),
            *([ChargePriceLevelSelect(house)] if house.grid_charging else []),
        ],
    )


class OnFailureSelect(ControlEntity, SelectEntity, RestoreEntity):
    """What the House does with its Inverters when its control has failed."""

    _attr_translation_key = "on_failure"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, house: House) -> None:
        """Create the select."""
        super().__init__(house, "on_failure", "select")
        self._attr_options = list(ON_FAILURE_OPTIONS)

    async def async_added_to_hass(self) -> None:
        """Take over the choice the owner left, if any."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ON_FAILURE_OPTIONS:
            self.house.control.on_failure = last.state

    @property
    def current_option(self) -> str:
        """The current choice."""
        return self.house.control.on_failure

    async def async_select_option(self, option: str) -> None:
        """Change the choice; it applies the next time the House fails."""
        self.house.control.on_failure = option
        self.async_write_ha_state()


class ChargePriceLevelSelect(ChargingEntity, SelectEntity, RestoreEntity):
    """The Price Levels at which Grid Charging may charge."""

    _attr_translation_key = "charge_price_levels"

    def __init__(self, house: House) -> None:
        """Create the select."""
        super().__init__(house, "charge_price_levels", "select")
        self._attr_options = list(PRICE_LEVEL_OPTIONS)

    async def async_added_to_hass(self) -> None:
        """Take over the choice the owner left, if any."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in PRICE_LEVEL_OPTIONS:
            self.charging.price_levels = last.state

    @property
    def current_option(self) -> str:
        """The current choice."""
        return self.charging.price_levels

    async def async_select_option(self, option: str) -> None:
        """Change the choice; the plan is made again."""
        self.charging.set_price_levels(option)
