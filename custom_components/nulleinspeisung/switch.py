"""Switches of a DTU and of a House."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NulleinspeisungConfigEntry
from .coordinator import DtuCoordinator
from .entity import DtuEntity, HouseEntity, setup_dtu_entities, setup_house_entities
from .house import House


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the automatic-restart switch of every DTU."""
    setup_dtu_entities(
        entry, async_add_entities, lambda c: [DtuAutomaticRestartSwitch(c)]
    )
    setup_house_entities(
        entry,
        async_add_entities,
        lambda house: [
            CurtailmentSwitch(house),
            *(
                [PublishGridPowerSwitch(house)]
                if house.grid_publisher is not None
                else []
            ),
        ],
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


class CurtailmentSwitch(HouseEntity, SwitchEntity, RestoreEntity):
    """Whether the House curtails its Inverters to the Feed-in Setpoint."""

    _attr_translation_key = "curtailment"

    def __init__(self, house: House) -> None:
        """Create the switch."""
        super().__init__(house, "curtailment")

    async def async_added_to_hass(self) -> None:
        """Restore the position the owner left it in; off by default."""
        await super().async_added_to_hass()
        self.async_on_remove(self.house.control.async_add_listener(self._handle_change))
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            # The loop starts once all entities have restored their values.
            self.house.control.curtailment = True

    @property
    def is_on(self) -> bool:
        """Whether Curtailment is switched on."""
        return self.house.control.curtailment

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch Curtailment on."""
        await self.house.control.async_set_curtailment(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch Curtailment off; the Inverters return to 100 %."""
        await self.house.control.async_set_curtailment(False)
        self.async_write_ha_state()


class PublishGridPowerSwitch(HouseEntity, SwitchEntity, RestoreEntity):
    """Whether the House hands its Grid Power to the AC Battery."""

    _attr_translation_key = "publish_grid_power"

    def __init__(self, house: House) -> None:
        """Create the switch."""
        super().__init__(house, "publish_grid_power")
        assert house.grid_publisher is not None
        self._publisher = house.grid_publisher

    async def async_added_to_hass(self) -> None:
        """Restore the position the owner left it in; off by default."""
        await super().async_added_to_hass()
        self.async_on_remove(self._publisher.async_add_listener(self._handle_change))
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            # Publishing starts once all entities have restored their values.
            self._publisher.enabled = True

    @property
    def is_on(self) -> bool:
        """Whether publishing is switched on."""
        return self._publisher.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start publishing."""
        await self._publisher.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop publishing."""
        await self._publisher.async_set_enabled(False)
        self.async_write_ha_state()
