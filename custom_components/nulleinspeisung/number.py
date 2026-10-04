"""Numbers of a House: the owner's control settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NulleinspeisungConfigEntry
from .entity import HouseEntity, setup_house_entities
from .house import House
from .house_control import (
    DEFAULT_FEED_IN_SETPOINT,
    DEFAULT_LIMIT_FLOOR,
    DEFAULT_TOLERANCE_BAND,
    DEFAULT_UPDATE_INTERVAL,
    HouseControl,
)


@dataclass(frozen=True, kw_only=True)
class HouseNumberDescription(NumberEntityDescription):
    """Describes a setting of a House's control."""

    default: float
    getter: Callable[[HouseControl], float]
    setter: Callable[[HouseControl, float], None]


def _set_attr(name: str) -> Callable[[HouseControl, float], None]:
    return lambda control, value: setattr(control, name, value)


DESCRIPTIONS: tuple[HouseNumberDescription, ...] = (
    HouseNumberDescription(
        key="feed_in_setpoint",
        translation_key="feed_in_setpoint",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=-5000,
        native_max_value=20000,
        native_step=10,
        mode=NumberMode.BOX,
        default=DEFAULT_FEED_IN_SETPOINT,
        getter=lambda control: control.feed_in_setpoint,
        setter=_set_attr("feed_in_setpoint"),
    ),
    HouseNumberDescription(
        key="update_interval",
        translation_key="update_interval",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=5,
        native_max_value=60,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_UPDATE_INTERVAL,
        getter=lambda control: control.update_interval,
        setter=lambda control, value: control.set_update_interval(value),
    ),
    HouseNumberDescription(
        key="tolerance_band",
        translation_key="tolerance_band",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=500,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_TOLERANCE_BAND,
        getter=lambda control: control.tolerance_band,
        setter=_set_attr("tolerance_band"),
    ),
    HouseNumberDescription(
        key="limit_floor",
        translation_key="limit_floor",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=2,
        native_max_value=50,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        default=DEFAULT_LIMIT_FLOOR,
        getter=lambda control: control.limit_floor,
        setter=_set_attr("limit_floor"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NulleinspeisungConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the control settings of every House."""
    setup_house_entities(
        entry,
        async_add_entities,
        lambda house: [HouseNumber(house, d) for d in DESCRIPTIONS],
    )


class HouseNumber(HouseEntity, RestoreNumber):
    """A setting of a House's control, kept across restarts."""

    entity_description: HouseNumberDescription

    def __init__(self, house: House, description: HouseNumberDescription) -> None:
        """Create the number."""
        super().__init__(house, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Take over the value the owner left, if any."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            description = self.entity_description
            value = float(last.native_value)
            if description.native_min_value <= value <= description.native_max_value:
                description.setter(self.house.control, value)

    @property
    def native_value(self) -> float:
        """The current setting."""
        return self.entity_description.getter(self.house.control)

    async def async_set_native_value(self, value: float) -> None:
        """Change the setting; it takes effect from the next run."""
        self.entity_description.setter(self.house.control, value)
        self.async_write_ha_state()
