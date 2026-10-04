"""Tests for the Nulleinspeisung config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nulleinspeisung.const import DOMAIN


async def test_config_flow_user_step_shows_form(hass: HomeAssistant) -> None:
    """Starting a user flow shows a form."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_config_flow_user_step_submits_creates_entry(
    hass: HomeAssistant,
) -> None:
    """Submitting the form creates a config entry titled 'Nulleinspeisung'."""
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nulleinspeisung"


async def test_config_flow_single_instance_only(hass: HomeAssistant) -> None:
    """Starting a second user flow while an entry exists aborts with reason."""
    MockConfigEntry(domain=DOMAIN, data={}).add_to_hass(hass)
    result: ConfigFlowResult = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
