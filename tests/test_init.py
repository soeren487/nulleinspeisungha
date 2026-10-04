"""Tests for Nulleinspeisung integration setup and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nulleinspeisung.const import DOMAIN


async def test_entry_setup_reaches_loaded(hass: HomeAssistant) -> None:
    """A config entry sets up and reaches state LOADED."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.LOADED


async def test_entry_unload_reaches_not_loaded(hass: HomeAssistant) -> None:
    """Entry unloads and reaches state NOT_LOADED."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state == ConfigEntryState.NOT_LOADED
