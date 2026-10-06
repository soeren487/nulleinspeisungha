"""Publishing a House's Grid Power to the AC Battery's virtual grid meter.

The grid meter driver on the GX (``dbus-mqtt-grid``) listens on one topic of the
owner's general MQTT broker for ``{"grid": {"power": <W>}}``, import positive.
It exits when no message arrived for 60 seconds, which leaves the AC Battery
without a grid meter.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components import mqtt
from homeassistant.core import CALLBACK_TYPE, Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .battery_watch import ISSUE_MQTT_NEEDED, ISSUE_OTHER_PUBLISHER, sync_issue

if TYPE_CHECKING:
    from .house import House

_LOGGER = logging.getLogger(__name__)

OWN_WINDOW = 10.0
"""Seconds during which a message with a payload just sent counts as our own."""
FOREIGN_QUIET = 120.0
"""Seconds without a foreign message after which its issue is cleared."""


class OwnMessages:
    """Remembers what was published lately, to tell the echo from other senders.

    No Home Assistant, no I/O; the time is passed in (seconds, one clock).
    """

    def __init__(self, window: float = OWN_WINDOW) -> None:
        """Remember payloads for ``window`` seconds."""
        self._window = window
        self._sent: list[tuple[float, str]] = []

    def sent(self, payload: str, now: float) -> None:
        """Note that ``payload`` was published at ``now``."""
        self._prune(now)
        self._sent.append((now, payload))

    def is_mine(self, payload: str, now: float) -> bool:
        """Whether ``payload`` is one this House published within the window."""
        self._prune(now)
        return any(p == payload for _, p in self._sent)

    def _prune(self, now: float) -> None:
        self._sent = [(t, p) for t, p in self._sent if now - t <= self._window]


def mqtt_available(hass: Any) -> bool:
    """Whether Home Assistant's MQTT integration is set up."""
    return bool(hass.config_entries.async_loaded_entries("mqtt"))


def grid_payload(watts: float) -> str:
    """The message the virtual grid meter expects."""
    return json.dumps({"grid": {"power": round(watts, 1) + 0.0}})


class GridPublisher:
    """Publishes the Grid Power of one House while the owner has switched it on."""

    def __init__(self, house: House, topic: str) -> None:
        """Create the publisher; it is off until ``enabled`` is set."""
        self._house = house
        self.topic = topic
        self.enabled = False
        """Whether publishing is switched on."""
        self.last_published: float | None = None
        """The last Grid Power published in W; ``None`` while off or before any."""
        self._own = OwnMessages()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._mqtt_unsub: CALLBACK_TYPE | None = None
        self._subscribing = False
        self._quiet_unsub: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` when switching or publishing happened."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- life cycle --------------------------------------------------------

    @callback
    def start(self) -> None:
        """Begin following the Grid Meter if publishing was restored as on."""
        if self.enabled:
            self._activate()

    @callback
    def stop(self) -> None:
        """Stop following; the switch position and the issues stay as they are."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        self._drop_subscription()
        if self._quiet_unsub is not None:
            self._quiet_unsub()
            self._quiet_unsub = None

    async def async_set_enabled(self, enabled: bool) -> None:
        """Switch publishing on or off."""
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            self._activate()
        else:
            self.stop()
            self.last_published = None
            sync_issue(self._house, ISSUE_MQTT_NEEDED, False)
            sync_issue(self._house, ISSUE_OTHER_PUBLISHER, False)
        self._notify()

    def _activate(self) -> None:
        meter = [self._house.config.grid_meter]
        hass = self._house.hass
        self._unsubs = [
            async_track_state_change_event(hass, meter, self._on_meter),
            # A report with an unchanged value is not a state change.
            async_track_state_report_event(hass, meter, self._on_meter),
        ]
        self._check_mqtt()
        hass.async_create_task(self._async_subscribe())

    # -- publishing ----------------------------------------------------------

    def _check_mqtt(self) -> bool:
        """Raise or clear the issue about a missing MQTT integration."""
        available = mqtt_available(self._house.hass)
        sync_issue(self._house, ISSUE_MQTT_NEEDED, not available)
        return available

    @callback
    def _on_meter(self, _event: Event[Any]) -> None:
        # Only a report of the meter publishes. There is deliberately no timer
        # that repeats the last value: when the meter stops reporting, nothing
        # more is published and the driver's 60 s timeout is the intended
        # dead-man that leaves the AC Battery idle. A repeat timer would hide
        # a failed Grid Meter from the battery.
        power = self._house.grid_power()
        if power is None or not self._check_mqtt():
            return
        if self._mqtt_unsub is None:
            self._house.hass.async_create_task(self._async_subscribe())
        payload = grid_payload(power)
        self._own.sent(payload, dt_util.utcnow().timestamp())
        self._house.hass.async_create_task(
            self._async_publish(payload, round(power, 1) + 0.0)
        )

    async def _async_publish(self, payload: str, watts: float) -> None:
        try:
            await mqtt.async_publish(self._house.hass, self.topic, payload, 0, False)
        except HomeAssistantError:
            _LOGGER.warning("Publishing the grid power failed", exc_info=True)
            return
        if self.enabled:
            self.last_published = watts
            self._notify()

    # -- the second publisher ------------------------------------------------

    async def _async_subscribe(self) -> None:
        if (
            not self.enabled
            or self._mqtt_unsub is not None
            or self._subscribing
            or not mqtt_available(self._house.hass)
        ):
            return
        self._subscribing = True
        try:
            unsub = await mqtt.async_subscribe(
                self._house.hass, self.topic, self._on_message, 0
            )
        except HomeAssistantError:
            _LOGGER.warning("Subscribing to the grid topic failed", exc_info=True)
            return
        finally:
            self._subscribing = False
        if self.enabled and self._unsubs:
            self._mqtt_unsub = unsub
        else:
            unsub()

    def _drop_subscription(self) -> None:
        if self._mqtt_unsub is not None:
            self._mqtt_unsub()
            self._mqtt_unsub = None

    @callback
    def _on_message(self, message: Any) -> None:
        payload = message.payload
        if not isinstance(payload, str):
            payload = bytes(payload).decode(errors="replace")
        if self._own.is_mine(payload, dt_util.utcnow().timestamp()):
            return
        sync_issue(self._house, ISSUE_OTHER_PUBLISHER, True)
        if self._quiet_unsub is not None:
            self._quiet_unsub()
        self._quiet_unsub = async_call_later(
            self._house.hass, timedelta(seconds=FOREIGN_QUIET), self._on_quiet
        )

    @callback
    def _on_quiet(self, _now: object) -> None:
        self._quiet_unsub = None
        sync_issue(self._house, ISSUE_OTHER_PUBLISHER, False)
