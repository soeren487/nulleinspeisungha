"""Switches of a DTU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .entity import DtuEntity, setup_dtu_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the automatic-restart switch of every DTU."""
    setup_dtu_entities(
        entry, async_add_entities, lambda c: [DtuAutomaticRestartSwitch(c)]
    )


class DtuAutomaticRestartSwitch(DtuEntity, SwitchEntity, RestoreEntity):
    """Whether a Stuck DTU is restarted without asking the owner."""

    _attr_translation_key = "automatic_restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DtuCoordinator) -> None:
        """Create the switch."""
        super().__init__(coordinator, "automatic_restart")

    async def async_added_to_hass(self) -> None:
        """Restore the position the owner left it in; on by default."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self.coordinator.supervisor.automatic_restart = False
        # Only now is it known whether the owner switched it off.
        self.coordinator.supervisor.arm()

    @property
    def available(self) -> bool:
        """The switch can be used while the DTU does not answer."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether automatic restart is enabled."""
        return self.coordinator.supervisor.automatic_restart

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic restart."""
        self.coordinator.supervisor.automatic_restart = True
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic restart."""
        self.coordinator.supervisor.automatic_restart = False
        self.coordinator.async_update_listeners()
