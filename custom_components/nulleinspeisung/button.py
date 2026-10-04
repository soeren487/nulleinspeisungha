"""Buttons of a DTU."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .entity import DtuEntity, setup_dtu_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the restart button of every DTU."""
    setup_dtu_entities(entry, async_add_entities, lambda c: [DtuRestartButton(c)])


class DtuRestartButton(DtuEntity, ButtonEntity):
    """Restarts the DTU immediately."""

    _attr_translation_key = "restart"

    def __init__(self, coordinator: DtuCoordinator) -> None:
        """Create the restart button."""
        super().__init__(coordinator, "restart")

    async def async_press(self) -> None:
        """Restart the DTU now."""
        await self.coordinator.supervisor.async_restart()
        self.coordinator.async_update_listeners()
