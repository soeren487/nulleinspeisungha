"""The AC battery gateway: one Victron GX, read over its own MQTT broker.

The integration opens its own connection to the broker built into the GX
(ADR 0002). Everything above ``MqttTransport`` is shared between production
and tests; the transport is the only thing a test replaces.

Besides the keepalive, ``async_write`` publishes a value to a path of the GX.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Protocol

import paho.mqtt.client as mqtt
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

FRESH_SECONDS = 60.0
"""A state is fresh when a value arrived this recently."""
KEEPALIVE_SECONDS = 30.0
"""Seconds between two keepalive messages."""
CHECK_SECONDS = 10.0
"""How often freshness is re-evaluated, since no message announces silence."""
PROBE_TIMEOUT = 10.0
"""Seconds before a probe of a GX is given up."""

KEEPALIVE_FIRST = b""
KEEPALIVE_NEXT = b'{ "keepalive-options" : ["suppress-republish"] }'

_SERIAL_FILTER = "N/+/system/0/Serial"
_SERIAL_TOPIC = re.compile(r"^N/([^/]+)/system/0/Serial$")
_MAX_CHARGE_CURRENT = re.compile(r"^battery/([^/]+)/Info/MaxChargeCurrent$")

_PATH_FIELDS = {
    "system/0/Dc/Battery/Soc": "charge_level",
    "system/0/Dc/Battery/Power": "power",
    "system/0/Dc/Battery/Voltage": "voltage",
    "settings/0/Settings/CGwacs/AcPowerSetPoint": "grid_setpoint",
    "settings/0/Settings/DynamicEss/Mode": "dynamic_ess_mode",
    "hub4/0/Overrides/Setpoint": "setpoint_override",
    "hub4/0/Overrides/MaxDischargePower": "max_discharge_override",
}
_SUBSCRIBED_PATHS = (*_PATH_FIELDS, "battery/+/Info/MaxChargeCurrent")


class GxConnectionError(Exception):
    """The GX could not be reached or did not identify itself."""


class MqttTransport(Protocol):
    """What the gateway needs from an MQTT client.

    The handlers are called on the event loop. The transport connects by
    itself again after losing the connection and then calls ``on_connect``
    once more; subscriptions do not survive, the gateway repeats them.
    """

    def set_handlers(
        self,
        *,
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
        on_message: Callable[[str, bytes], None],
    ) -> None:
        """Say who to call; before ``async_connect``."""

    async def async_connect(self, host: str, port: int) -> None:
        """Start connecting without waiting; ``on_connect`` reports success."""

    def subscribe(self, topic: str) -> None:
        """Subscribe to a topic filter."""

    def publish(self, topic: str, payload: bytes) -> None:
        """Publish a message."""

    async def async_close(self) -> None:
        """Disconnect and stop for good."""


class PahoTransport:
    """``MqttTransport`` on paho-mqtt, whose network loop runs in its own thread."""

    def __init__(self) -> None:
        """Create the client; nothing is connected yet."""
        self._loop = asyncio.get_running_loop()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._on_connect: Callable[[], None] = lambda: None
        self._on_disconnect: Callable[[], None] = lambda: None
        self._on_message: Callable[[str, bytes], None] = lambda topic, payload: None
        self._client.on_connect = self._paho_connect
        self._client.on_disconnect = self._paho_disconnect
        self._client.on_message = self._paho_message

    def set_handlers(
        self,
        *,
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
        on_message: Callable[[str, bytes], None],
    ) -> None:
        """Say who to call."""
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message = on_message

    def _hop(self, func: Callable[..., None], *args: Any) -> None:
        """Run ``func`` on the event loop from paho's thread."""
        with contextlib.suppress(RuntimeError):  # the loop is closed
            self._loop.call_soon_threadsafe(func, *args)

    def _paho_connect(
        self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _props: Any
    ) -> None:
        if not reason_code.is_failure:
            self._hop(self._on_connect)

    def _paho_disconnect(
        self, _client: Any, _userdata: Any, _flags: Any, _reason_code: Any, _props: Any
    ) -> None:
        self._hop(self._on_disconnect)

    def _paho_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        self._hop(self._on_message, message.topic, bytes(message.payload))

    async def async_connect(self, host: str, port: int) -> None:
        """Connect in paho's thread, retrying until it works."""
        self._client.connect_async(host, port, keepalive=60)
        self._client.loop_start()

    def subscribe(self, topic: str) -> None:
        """Subscribe to a topic filter."""
        self._client.subscribe(topic)

    def publish(self, topic: str, payload: bytes) -> None:
        """Publish a message; paho queues it for its thread."""
        self._client.publish(topic, payload)

    async def async_close(self) -> None:
        """Disconnect and stop paho's thread without blocking the loop."""
        self._on_connect = self._on_disconnect = lambda: None
        self._on_message = lambda topic, payload: None
        await self._loop.run_in_executor(None, self._stop)

    def _stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


def create_transport() -> MqttTransport:
    """A new transport; tests replace this function."""
    return PahoTransport()


@dataclass(frozen=True, slots=True)
class BatteryState:
    """What is known about the AC Battery; ``None`` means unknown."""

    charge_level: float | None = None
    """State of charge in percent."""
    power: float | None = None
    """Battery power in W, positive while charging."""
    voltage: float | None = None
    """Battery voltage in V."""
    charge_current_limit: float | None = None
    """The BMS's charge current limit in A."""
    grid_setpoint: float | None = None
    """The stored grid setpoint of the GX in W, positive for import."""
    dynamic_ess_mode: int | None = None
    """0 means Dynamic ESS is off."""
    setpoint_override: float | None = None
    """The grid setpoint override in W; ``None`` when not set."""
    max_discharge_override: float | None = None
    """The maximum discharge power override in W; ``None`` when not set."""
    connected: bool = False
    """Whether the GX's broker is connected."""
    fresh: bool = False
    """Connected and a value arrived within the last 60 s."""


def parse_value(payload: bytes) -> float | None:
    """The ``value`` of a GX message; ``None`` for null or anything unusable."""
    try:
        value = json.loads(payload)["value"]
    except ValueError, TypeError, KeyError:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def parse_text(payload: bytes) -> str | None:
    """The ``value`` of a GX message when it is a non-empty string."""
    try:
        value = json.loads(payload)["value"]
    except ValueError, TypeError, KeyError:
        return None
    return value if isinstance(value, str) and value else None


def _subscription_matches(topic_filter: str, topic: str) -> bool:
    """Whether an MQTT topic filter with ``+`` matches a topic."""
    filter_parts, topic_parts = topic_filter.split("/"), topic.split("/")
    return len(filter_parts) == len(topic_parts) and all(
        f in ("+", t) for f, t in zip(filter_parts, topic_parts, strict=True)
    )


class BatteryGateway:
    """The AC Battery of one House, read from its GX."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        portal_id: str | None = None,
    ) -> None:
        """Create the gateway; ``async_start`` connects.

        Without a ``portal_id`` the gateway learns it from the GX.
        """
        self._hass = hass
        self.host = host
        self.port = port
        self.portal_id = portal_id
        self._transport: MqttTransport | None = None
        self._values = BatteryState()
        self._current_limits: dict[str, float | None] = {}
        self._connected = False
        self._last_value_at: float | None = None
        self._published: BatteryState | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    # -- observers ---------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` when the state changes; returns the remover."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @property
    def state(self) -> BatteryState:
        """The current state."""
        return replace(self._values, connected=self._connected, fresh=self._is_fresh())

    def _is_fresh(self) -> bool:
        if not self._connected or self._last_value_at is None:
            return False
        age = dt_util.utcnow().timestamp() - self._last_value_at
        return age <= FRESH_SECONDS

    @callback
    def _changed(self) -> None:
        state = self.state
        if state == self._published:
            return
        self._published = state
        for listener in list(self._listeners):
            listener()

    # -- life cycle --------------------------------------------------------

    async def async_start(self) -> None:
        """Connect to the GX; the transport keeps reconnecting by itself."""
        transport = create_transport()
        transport.set_handlers(
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_message=self._on_message,
        )
        self._transport = transport
        self._unsubs = [
            async_track_time_interval(
                self._hass, self._keepalive, timedelta(seconds=KEEPALIVE_SECONDS)
            ),
            async_track_time_interval(
                self._hass, self._check, timedelta(seconds=CHECK_SECONDS)
            ),
        ]
        await transport.async_connect(self.host, self.port)

    async def async_stop(self) -> None:
        """Stop the timers and close the connection."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        transport, self._transport = self._transport, None
        self._connected = False
        if transport is not None:
            await transport.async_close()

    # -- transport events --------------------------------------------------

    @callback
    def _on_connect(self) -> None:
        if self._transport is None:
            return
        self._connected = True
        if self.portal_id is None:
            self._transport.subscribe(_SERIAL_FILTER)
        else:
            self._subscribe_values()
        self._changed()

    @callback
    def _on_disconnect(self) -> None:
        self._connected = False
        self._changed()

    @callback
    def _on_message(self, topic: str, payload: bytes) -> None:
        if self.portal_id is None:
            match = _SERIAL_TOPIC.match(topic)
            serial = parse_text(payload) if match else None
            if match and serial:
                self.portal_id = match.group(1)
                self._subscribe_values()
            return
        prefix = f"N/{self.portal_id}/"
        if not topic.startswith(prefix):
            return
        path = topic[len(prefix) :]
        value = parse_value(payload)
        if (field := _PATH_FIELDS.get(path)) is not None:
            if field == "dynamic_ess_mode":
                self._values = replace(
                    self._values,
                    dynamic_ess_mode=None if value is None else int(value),
                )
            else:
                self._values = replace(self._values, **{field: value})
        elif (match := _MAX_CHARGE_CURRENT.match(path)) is not None:
            self._current_limits[match.group(1)] = value
            known = [v for v in self._current_limits.values() if v is not None]
            self._values = replace(
                self._values,
                charge_current_limit=min(known) if known else None,
            )
        else:
            return
        self._last_value_at = dt_util.utcnow().timestamp()
        self._changed()

    def _subscribe_values(self) -> None:
        """Subscribe to what is needed, then ask the GX for everything."""
        assert self._transport is not None
        for path in _SUBSCRIBED_PATHS:
            self._transport.subscribe(f"N/{self.portal_id}/{path}")
        self._publish(f"R/{self.portal_id}/keepalive", KEEPALIVE_FIRST)

    # -- timers ------------------------------------------------------------

    async def _keepalive(self, _now: object) -> None:
        if self._connected and self.portal_id is not None:
            self._publish(f"R/{self.portal_id}/keepalive", KEEPALIVE_NEXT)

    async def _check(self, _now: object) -> None:
        self._changed()

    def async_write(self, path: str, value: float | None) -> bool:
        """Write ``value`` (``None`` writes null) to ``path`` on the GX.

        Publishes ``{"value": value}`` to ``W/<portal id>/<path>``. Returns
        whether it was sent; nothing is sent while the GX is not connected.
        """
        if not self._connected or self.portal_id is None:
            return False
        payload = json.dumps({"value": value}).encode()
        self._publish(f"W/{self.portal_id}/{path}", payload)
        return True

    def _publish(self, topic: str, payload: bytes) -> None:
        """The single place anything leaves for the GX."""
        if self._transport is not None:
            self._transport.publish(topic, payload)


async def async_probe(host: str, port: int) -> str:
    """Connect to a GX and return its portal id.

    Raises ``GxConnectionError`` when it cannot be reached or does not name
    itself within ``PROBE_TIMEOUT`` seconds (about 10).
    """
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[str] = loop.create_future()
    transport = create_transport()

    def on_connect() -> None:
        transport.subscribe(_SERIAL_FILTER)

    def on_message(topic: str, payload: bytes) -> None:
        match = _SERIAL_TOPIC.match(topic)
        if match and parse_text(payload) and not answer.done():
            answer.set_result(match.group(1))

    transport.set_handlers(
        on_connect=on_connect, on_disconnect=lambda: None, on_message=on_message
    )
    try:
        await transport.async_connect(host, port)
        async with asyncio.timeout(PROBE_TIMEOUT):
            return await answer
    except TimeoutError, OSError:
        raise GxConnectionError(f"{host}:{port} does not answer") from None
    finally:
        await transport.async_close()
