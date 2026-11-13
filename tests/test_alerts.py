"""Repair issues that are also sent to the notification target."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.nulleinspeisung.alerts import raise_alert, resolve_alert
from custom_components.nulleinspeisung.const import CONF_NOTIFY_TARGET, DOMAIN

ISSUE = "grid_meter_failure_house-home"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_NOTIFY_TARGET: "notify.owner"})
    entry.add_to_hass(hass)
    return entry


async def test_a_flapping_issue_is_sent_once_an_hour(
    hass: HomeAssistant, freezer
) -> None:
    calls = async_mock_service(hass, "notify", "send_message")
    entry = _entry(hass)

    def flap() -> None:
        raise_alert(
            hass,
            entry,
            ISSUE,
            "grid_meter_failure",
            {"house": "Home"},
            "Grid Meter of House {house} delivers no data",
        )

    flap()
    await hass.async_block_till_done()
    assert len(calls) == 1

    for _ in range(3):
        resolve_alert(hass, ISSUE)
        freezer.tick(timedelta(minutes=10))
        flap()
        await hass.async_block_till_done()
    assert len(calls) == 1

    resolve_alert(hass, ISSUE)
    freezer.tick(timedelta(minutes=31))
    flap()
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_two_issues_do_not_hold_each_other_back(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "send_message")
    entry = _entry(hass)
    for issue in ("one", "two"):
        raise_alert(hass, entry, issue, "dtu_failure", {"house": "Home"}, "{house}")
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_a_notification_text_that_cannot_be_formatted_is_logged_not_raised(
    hass: HomeAssistant, caplog
) -> None:
    calls = async_mock_service(hass, "notify", "send_message")
    entry = _entry(hass)
    caplog.set_level(logging.ERROR)

    raise_alert(hass, entry, ISSUE, "no_such_issue", {}, "Missing {house}")
    await hass.async_block_till_done()

    assert calls == []
    assert any("notification failed" in r.getMessage() for r in caplog.records)
