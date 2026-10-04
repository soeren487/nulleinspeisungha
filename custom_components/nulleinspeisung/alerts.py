"""Repair issues that are also sent to the notification target."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .const import CONF_NOTIFY_TARGET, DOMAIN

_LOGGER = logging.getLogger(__name__)


def resolve_alert(hass: HomeAssistant, issue_id: str) -> None:
    """Remove a repair issue, if it exists."""
    ir.async_delete_issue(hass, DOMAIN, issue_id)


def raise_alert(
    hass: HomeAssistant,
    entry: ConfigEntry,
    issue_id: str,
    translation_key: str,
    placeholders: dict[str, str],
    english_title: str,
) -> None:
    """Raise a repair issue, and tell the notification target once.

    Nothing happens while the issue already exists. ``english_title`` is the
    title used for the notification when no translation can be loaded.
    """
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None:
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )
    target = entry.options.get(CONF_NOTIFY_TARGET)
    if target:
        hass.async_create_task(
            _async_notify(hass, target, translation_key, placeholders, english_title)
        )


async def _async_notify(
    hass: HomeAssistant,
    target: str,
    translation_key: str,
    placeholders: dict[str, str],
    english_title: str,
) -> None:
    """Send the translated title of an issue to the notification target."""
    title = f"component.{DOMAIN}.issues.{translation_key}.title"
    translations = await async_get_translations(
        hass, hass.config.language, "issues", [DOMAIN]
    )
    text = translations.get(title, english_title).format(**placeholders)
    try:
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": target, "message": text},
            blocking=True,
        )
    except Exception:
        _LOGGER.exception("Sending the notification failed")
