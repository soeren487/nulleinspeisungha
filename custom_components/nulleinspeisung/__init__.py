"""The Nulleinspeisung integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

type NulleinspeisungConfigEntry = ConfigEntry[None]


async def async_setup_entry(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> bool:
    """Set up Nulleinspeisung from a config entry."""
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NulleinspeisungConfigEntry
) -> bool:
    """Unload a config entry."""
    return True
