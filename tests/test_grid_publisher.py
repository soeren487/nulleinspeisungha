"""Handing the House's Grid Power to the AC Battery, as the owner sees it."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_fire_mqtt_message,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.nulleinspeisung.const import DOMAIN, SIGN_EXPORT
from custom_components.nulleinspeisung.grid_publisher import OwnMessages
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

METER = "sensor.grid_meter"
TOPIC = "grid/test/house"
SWITCH = "switch.home_publish_grid_power"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _payload(watts: float) -> str:
    return f'{{"grid": {{"power": {watts}}}}}'


async def _home(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    gx: SimGx | None = None,
    sign: str = "import",
    topic: str | None = TOPIC,
    on: bool = True,
):
    if on:
        mock_restore_cache(hass, [State(SWITCH, "on")])
    gx = gx or SimGx()
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("Home", gx=gx, sign=sign, grid_topic=topic)],
    )
    return entry, gx


def _report(hass: HomeAssistant, value: str, unit: str = "W") -> None:
    hass.states.async_set(METER, value, {"unit_of_measurement": unit})


async def _tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(delta=timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant, kind: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_house-home")


def _sent(mqtt_mock: MagicMock) -> list[tuple]:
    return [c.args for c in mqtt_mock.async_publish.call_args_list]


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"house-home_{key}"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


# -- the pure helper ---------------------------------------------------------


def test_own_payload_is_recognised_within_the_window_only() -> None:
    """A payload just sent is mine; another one, or an old one, is not."""
    own = OwnMessages(window=10)
    own.sent('{"grid": {"power": 5.0}}', now=100.0)
    assert own.is_mine('{"grid": {"power": 5.0}}', now=105.0)
    assert not own.is_mine('{"grid": {"power": 6.0}}', now=105.0)
    assert not own.is_mine('{"grid": {"power": 5.0}}', now=111.0)


# -- publishing ----------------------------------------------------------------


async def test_every_meter_report_is_published(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Watts, import positive, one decimal, QoS 0, not retained; repeats too."""
    await _home(hass, dtu_network)
    _report(hass, "-123.46")
    await hass.async_block_till_done()
    _report(hass, "-123.46")  # an unchanged value is a report as well
    await hass.async_block_till_done()
    _report(hass, "250")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [
        (TOPIC, _payload(-123.5), 0, False),
        (TOPIC, _payload(-123.5), 0, False),
        (TOPIC, _payload(250.0), 0, False),
    ]
    assert _state(hass, "sensor", "reported_grid_power") == "250.0"


async def test_export_sign_is_turned_into_import_positive(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """A meter reporting export as positive is published as negative import."""
    await _home(hass, dtu_network, sign=SIGN_EXPORT)
    _report(hass, "300")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, _payload(-300.0), 0, False)]


async def test_kilowatts_are_published_as_watts(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """A meter in kW is converted."""
    await _home(hass, dtu_network)
    _report(hass, "1.25", "kW")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, _payload(1250.0), 0, False)]


async def test_unknown_grid_power_publishes_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """An unavailable or unknown meter is not published."""
    await _home(hass, dtu_network)
    _report(hass, "unavailable")
    _report(hass, "unknown")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == []


async def test_a_silent_meter_stops_the_publishing(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """Nothing repeats the last value: the battery's own timeout takes over."""
    await _home(hass, dtu_network)
    _report(hass, "100")
    await hass.async_block_till_done()
    assert len(_sent(mqtt_mock)) == 1
    for _ in range(5):
        await _tick(hass, freezer, 60)
    assert len(_sent(mqtt_mock)) == 1


async def test_with_the_switch_off_nothing_is_published_or_written(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Stored off: the GX is not written to even with overrides set."""
    gx = SimGx()
    await _home(hass, dtu_network, gx, on=False)
    gx.set("hub4/0/Overrides/Setpoint", 100)
    gx.set("hub4/0/Overrides/MaxDischargePower", 0)
    _report(hass, "100")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == []
    assert gx.other_publishes == []
    assert _state(hass, "switch", "publish_grid_power") == "off"
    assert _state(hass, "sensor", "reported_grid_power") == "unknown"


async def test_switching_on_and_off_by_the_owner(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """The owner switches publishing on; the sensor knows the value; off clears it."""
    await _home(hass, dtu_network, on=False)
    _report(hass, "50")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == []
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    _report(hass, "60")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, _payload(60.0), 0, False)]
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    _report(hass, "70")
    await hass.async_block_till_done()
    assert len(_sent(mqtt_mock)) == 1
    assert _state(hass, "sensor", "reported_grid_power") == "unknown"


async def test_the_switch_survives_a_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """On stays on after the entry is reloaded."""
    entry, _ = await _home(hass, dtu_network, on=False)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "switch", "publish_grid_power") == "on"
    _report(hass, "10")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock) == [(TOPIC, _payload(10.0), 0, False)]


async def test_no_switch_without_topic_or_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """Houses that cannot publish have neither switch nor sensor."""
    await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[
            SimHouse("Home", gx=SimGx()),
            SimHouse("Other", grid_topic=TOPIC, grid_meter="sensor.other"),
        ],
    )
    registry = er.async_get(hass)
    for house in ("home", "other"):
        for platform, key in (
            ("switch", "publish_grid_power"),
            ("sensor", "reported_grid_power"),
        ):
            assert (
                registry.async_get_entity_id(platform, DOMAIN, f"house-{house}_{key}")
                is None
            )


async def test_without_the_mqtt_integration_an_issue_is_raised(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Nothing is published; a repair issue asks for the MQTT integration."""
    await _home(hass, dtu_network)
    _report(hass, "100")
    await hass.async_block_till_done()
    issue = _issue(hass, "mqtt_needed")
    assert issue is not None
    assert issue.translation_placeholders == {"house": "Home"}
    assert _state(hass, "sensor", "reported_grid_power") == "unknown"


# -- the second publisher ------------------------------------------------------


async def test_a_foreign_message_raises_an_issue_and_clears_after_quiet(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """Another sender on the topic is reported; our own echo is not."""
    await _home(hass, dtu_network)
    _report(hass, "100")
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, TOPIC, _payload(100.0))  # our own echo
    await hass.async_block_till_done()
    assert _issue(hass, "other_grid_publisher") is None
    async_fire_mqtt_message(hass, TOPIC, _payload(-5.0))
    await hass.async_block_till_done()
    assert _issue(hass, "other_grid_publisher") is not None
    await _tick(hass, freezer, 100)
    async_fire_mqtt_message(hass, TOPIC, _payload(-6.0))
    await hass.async_block_till_done()
    await _tick(hass, freezer, 100)
    assert _issue(hass, "other_grid_publisher") is not None
    await _tick(hass, freezer, 30)
    assert _issue(hass, "other_grid_publisher") is None
    # Publishing went on throughout.
    _report(hass, "101")
    await hass.async_block_till_done()
    assert _sent(mqtt_mock)[-1] == (TOPIC, _payload(101.0), 0, False)


# -- left-over overrides -----------------------------------------------------------


async def test_left_over_overrides_are_released_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock, freezer
) -> None:
    """One release per override, not repeated while waiting for the GX."""
    gx = SimGx()
    gx.accepts_writes = False  # the GX stays silent: no second write meanwhile
    gx.topics["hub4/0/Overrides/Setpoint"] = '{"value": -150}'
    gx.topics["hub4/0/Overrides/MaxDischargePower"] = '{"value": 0}'
    await _home(hass, dtu_network, gx)
    p = gx.portal_id
    release = [
        (f"W/{p}/hub4/0/Overrides/Setpoint", b'{"value": null}'),
        (f"W/{p}/hub4/0/Overrides/MaxDischargePower", b'{"value": -1}'),
    ]
    assert gx.other_publishes == release
    gx.set("system/0/Dc/Battery/Soc", 50)  # unrelated change
    await _tick(hass, freezer, 5)
    assert gx.other_publishes == release
    gx.set("hub4/0/Overrides/Setpoint", None)
    gx.set("hub4/0/Overrides/MaxDischargePower", None)
    await _tick(hass, freezer, 30)
    assert gx.other_publishes == release


async def test_overrides_found_later_are_released(
    hass: HomeAssistant, dtu_network: DtuNetwork, mqtt_mock: MagicMock
) -> None:
    """An override set while publishing is on is released when it shows."""
    gx = SimGx()
    await _home(hass, dtu_network, gx)
    assert gx.other_publishes == []
    gx.set("hub4/0/Overrides/Setpoint", 20)
    assert gx.other_publishes == [
        (f"W/{gx.portal_id}/hub4/0/Overrides/Setpoint", b'{"value": null}')
    ]
