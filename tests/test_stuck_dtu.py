"""End-to-end tests of Stuck DTU detection and restart through Home Assistant."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.nulleinspeisung.const import (
    CONF_NOTIFY_TARGET,
    CONF_RESTART_WAIT,
    CONF_STALENESS_TIME,
    CONF_SUN_ANGLE,
    CONF_URL,
    DOMAIN,
    DTU_UPDATE_INTERVAL,
)
from tests.conftest import DtuNetwork, SimDtu, Sun, setup_entry

SERIAL = "199980126212"
STEP = int(DTU_UPDATE_INTERVAL.total_seconds())


async def advance(
    hass: HomeAssistant,
    freezer,
    seconds: int,
    network: DtuNetwork | None = None,
    each: Callable[[], None] | None = None,
) -> None:
    """Let ``seconds`` pass, one polling interval at a time.

    With a ``network`` the simulated DTUs age their data after a reboot; ``each``
    is called after every interval.
    """
    for _ in range(seconds // STEP):
        freezer.tick(DTU_UPDATE_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        if network is not None:
            network.simulate()
        if each is not None:
            each()


def entity(hass: HomeAssistant, domain: str, key: str) -> str:
    """Entity id of a DTU entity."""
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{SERIAL}_{key}"
    )
    assert entity_id is not None
    return entity_id


def state(hass: HomeAssistant, domain: str, key: str) -> str:
    """State of a DTU entity."""
    return hass.states.get(entity(hass, domain, key)).state


def issue(hass: HomeAssistant, kind: str) -> ir.IssueEntry | None:
    """The repair issue of the DTU of the given kind, if raised."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{SERIAL}")


def stuck_dtu() -> SimDtu:
    """A DTU whose Inverters all report very old data."""
    return SimDtu.default()


def healthy_dtu() -> SimDtu:
    """A DTU with one Inverter that delivers fresh data."""
    dtu = SimDtu.default()
    dtu.inverters[0].data_age = 4
    return dtu


async def test_stuck_by_day_is_restarted_once_with_auth_and_body(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A silent DTU by day gets exactly one authenticated reboot request."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())
    await advance(hass, freezer, 30)

    reboots = dtu_network.reboots()
    assert len(reboots) == 1
    body, headers = reboots[0]
    assert body == {"data": json.dumps({"reboot": True})}
    expected = base64.b64encode(b"admin:password").decode()
    assert headers["Authorization"] == f"Basic {expected}"
    assert state(hass, "binary_sensor", "stuck") == STATE_ON


async def test_no_restart_at_night(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Silence at night is normal: no restart and no stuck indicator."""
    sun.elevation = -5
    await setup_entry(hass, dtu_network, stuck_dtu())
    await advance(hass, freezer, 60)

    assert dtu_network.reboots() == []
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF


async def test_no_restart_when_one_inverter_is_fresh(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """One Inverter with fresh data means the DTU is fine."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, healthy_dtu())
    await advance(hass, freezer, 60)

    assert dtu_network.reboots() == []
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF


async def test_no_second_restart_during_wait_then_repeat(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """After a restart nothing is sent for three minutes; then it is repeated."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())
    await advance(hass, freezer, 20)
    assert len(dtu_network.reboots()) == 1

    await advance(hass, freezer, 150)
    assert len(dtu_network.reboots()) == 1

    await advance(hass, freezer, 60)
    assert len(dtu_network.reboots()) == 2


async def test_backoff_and_repair_issue_after_three_unsuccessful_restarts(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """The fourth restart comes with an issue and a doubled wait."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())

    await advance(hass, freezer, 500)
    assert len(dtu_network.reboots()) == 3
    assert issue(hass, "restart_not_helping") is None

    await advance(hass, freezer, 100)
    assert len(dtu_network.reboots()) == 4
    raised = issue(hass, "restart_not_helping")
    assert raised is not None
    assert raised.translation_placeholders == {"dtu": "OpenDTU-Buero"}

    await advance(hass, freezer, 300)
    assert len(dtu_network.reboots()) == 4
    await advance(hass, freezer, 100)
    assert len(dtu_network.reboots()) == 5


async def test_recovery_clears_issue_and_stops_restarts(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Fresh data clears the issue and the stuck indicator."""
    sun.elevation = 30
    dtu = stuck_dtu()
    await setup_entry(hass, dtu_network, dtu)
    await advance(hass, freezer, 600)
    assert issue(hass, "restart_not_helping") is not None
    sent = len(dtu_network.reboots())

    dtu.inverters[1].data_age = 2
    dtu_network.apply()
    await advance(hass, freezer, 1200)

    assert issue(hass, "restart_not_helping") is None
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF
    assert len(dtu_network.reboots()) == sent


async def test_failed_restart_counts_as_unsuccessful(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A rejected reboot request is retried after the wait, and not counted."""
    sun.elevation = 30
    dtu = stuck_dtu()
    dtu.reboot_fails = True
    await setup_entry(hass, dtu_network, dtu)

    await advance(hass, freezer, 20)
    assert len(dtu_network.reboots()) == 1
    assert state(hass, "sensor", "restart_count") == "0"

    await advance(hass, freezer, 600)
    assert issue(hass, "restart_not_helping") is not None


async def test_automatic_restart_off_keeps_indicator_but_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """With the switch off the DTU is flagged stuck but never restarted."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())
    assert state(hass, "switch", "automatic_restart") == STATE_ON
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity(hass, "switch", "automatic_restart")},
        blocking=True,
    )
    await advance(hass, freezer, 60)

    assert dtu_network.reboots() == []
    assert state(hass, "binary_sensor", "stuck") == STATE_ON
    assert state(hass, "switch", "automatic_restart") == STATE_OFF


async def test_switch_position_survives_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A reload does not silently turn automatic restart back on."""
    sun.elevation = 30
    entry = await setup_entry(hass, dtu_network, stuck_dtu())
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity(hass, "switch", "automatic_restart")},
        blocking=True,
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, 60)

    assert state(hass, "switch", "automatic_restart") == STATE_OFF
    assert dtu_network.reboots() == []


async def test_button_restarts_now_and_counts_survive_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, sun: Sun
) -> None:
    """The button sends a restart; counter and time are shown and kept."""
    entry = await setup_entry(hass, dtu_network, healthy_dtu())
    assert state(hass, "sensor", "restart_count") == "0"
    assert state(hass, "sensor", "last_restart") == "unknown"

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity(hass, "button", "restart")},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(dtu_network.reboots()) == 1
    assert state(hass, "sensor", "restart_count") == "1"
    last = state(hass, "sensor", "last_restart")
    assert last not in ("unknown", STATE_UNAVAILABLE)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert state(hass, "sensor", "restart_count") == "1"
    assert state(hass, "sensor", "last_restart") == last


async def test_automatic_restart_counts_and_stamps(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Automatic restarts are counted too."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())
    await advance(hass, freezer, 200)

    assert state(hass, "sensor", "restart_count") == "2"
    assert state(hass, "sensor", "last_restart") not in ("unknown", STATE_UNAVAILABLE)


async def test_manual_restart_postpones_automatic_one(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A restart by the button starts the waiting time."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu())
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity(hass, "button", "restart")},
        blocking=True,
    )
    await advance(hass, freezer, 120)
    assert len(dtu_network.reboots()) == 1


async def test_unreachable_dtu_gets_no_restart_but_an_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A DTU that does not answer is not restarted; the owner is told later."""
    sun.elevation = 30
    dtu = healthy_dtu()
    await setup_entry(hass, dtu_network, dtu)
    dtu.down = True
    dtu_network.apply()

    await advance(hass, freezer, 90)
    assert issue(hass, "dtu_unreachable") is None
    assert state(hass, "binary_sensor", "stuck") == STATE_UNAVAILABLE

    await advance(hass, freezer, 60)
    raised = issue(hass, "dtu_unreachable")
    assert raised is not None
    assert raised.translation_placeholders == {"dtu": "OpenDTU-Buero"}
    assert dtu_network.reboots() == []

    dtu.down = False
    dtu_network.apply()
    await advance(hass, freezer, 20)
    assert issue(hass, "dtu_unreachable") is None
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF


async def test_notify_target_receives_text_once_per_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """With a target set, a raised issue is also sent as a message, once."""
    calls = async_mock_service(hass, "notify", "send_message")
    dtu = healthy_dtu()
    await setup_entry(
        hass, dtu_network, dtu, options={CONF_NOTIFY_TARGET: "notify.owner"}
    )
    dtu.down = True
    dtu_network.apply()
    await advance(hass, freezer, 200)

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.owner"
    assert "OpenDTU-Buero" in calls[0].data["message"]


async def test_no_notification_without_target(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Without a target only the repair issue appears."""
    calls = async_mock_service(hass, "notify", "send_message")
    dtu = healthy_dtu()
    await setup_entry(hass, dtu_network, dtu)
    dtu.down = True
    dtu_network.apply()
    await advance(hass, freezer, 200)

    assert issue(hass, "dtu_unreachable") is not None
    assert calls == []


async def test_options_flow_sets_and_clears_notify_target(
    hass: HomeAssistant, dtu_network: DtuNetwork, sun: Sun
) -> None:
    """The notification target is an optional option that can be changed."""
    entry = await setup_entry(hass, dtu_network, healthy_dtu())

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_TARGET: "notify.owner"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_TARGET] == "notify.owner"
    await hass.async_block_till_done()
    assert entry.state.value == "loaded"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_NOTIFY_TARGET not in entry.options


async def test_sun_angle_setting_is_honoured(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """With the sun angle at 40 degrees, a sun at 30 degrees is too low."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu(), settings={CONF_SUN_ANGLE: 40})
    await advance(hass, freezer, 60)
    assert dtu_network.reboots() == []

    sun.elevation = 41
    await advance(hass, freezer, 20)
    assert len(dtu_network.reboots()) == 1


async def test_staleness_time_setting_is_honoured(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Data of five minutes is still fresh with a staleness time of ten."""
    sun.elevation = 30
    dtu = SimDtu.default()
    for inverter in dtu.inverters:
        inverter.data_age = 300
    await setup_entry(hass, dtu_network, dtu, settings={CONF_STALENESS_TIME: 600})
    await advance(hass, freezer, 60)

    assert dtu_network.reboots() == []
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF


async def test_restart_wait_setting_is_honoured(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """With a wait of ten minutes the second restart comes after ten minutes."""
    sun.elevation = 30
    await setup_entry(hass, dtu_network, stuck_dtu(), settings={CONF_RESTART_WAIT: 600})
    await advance(hass, freezer, 400)
    assert len(dtu_network.reboots()) == 1

    await advance(hass, freezer, 300)
    assert len(dtu_network.reboots()) == 2


async def test_dtu_form_offers_settings_with_defaults(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Adding a DTU stores the defaults unless the owner changes them."""
    dtu_network.add(SimDtu.default())
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    async def add(extra: dict) -> dict:
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "dtu"), context={"source": "user"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            {CONF_URL: "opendtu.local", "password": "pw", **extra},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        return dict(list(entry.subentries.values())[-1].data)

    data = await add({})
    assert data[CONF_SUN_ANGLE] == 5
    assert data[CONF_STALENESS_TIME] == 120
    assert data[CONF_RESTART_WAIT] == 180


async def test_dtu_form_stores_chosen_settings_and_reconfigure_keeps_them(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Chosen settings are stored and offered again when reconfiguring."""
    dtu_network.add(SimDtu.default())
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "dtu"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "opendtu.local",
            "password": "pw",
            CONF_SUN_ANGLE: 12,
            CONF_STALENESS_TIME: 90,
            CONF_RESTART_WAIT: 240,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_SUN_ANGLE] == 12
    assert subentry.data[CONF_STALENESS_TIME] == 90
    assert subentry.data[CONF_RESTART_WAIT] == 240

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_URL: "opendtu.local", "password": "pw", CONF_RESTART_WAIT: 300},
    )
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data[CONF_RESTART_WAIT] == 300


async def test_unreachable_inverters_after_restart_back_off_and_alert(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """Data that looks fresh right after a restart does not hide the failure."""
    sun.elevation = 30
    dtu = stuck_dtu()
    dtu.after_reboot = "aging"
    await setup_entry(hass, dtu_network, dtu)
    stuck_entity = entity(hass, "binary_sensor", "stuck")

    def indicator_on() -> None:
        assert hass.states.get(stuck_entity).state == STATE_ON

    await advance(hass, freezer, 500, dtu_network, indicator_on)
    assert len(dtu_network.reboots()) == 3
    assert issue(hass, "restart_not_helping") is None

    await advance(hass, freezer, 100, dtu_network, indicator_on)
    assert len(dtu_network.reboots()) == 4
    assert issue(hass, "restart_not_helping") is not None

    await advance(hass, freezer, 300, dtu_network, indicator_on)
    assert len(dtu_network.reboots()) == 4
    await advance(hass, freezer, 100, dtu_network, indicator_on)
    assert len(dtu_network.reboots()) == 5


async def test_inverters_fresh_after_restart_means_one_restart_only(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A restart that works: one request, no issue, indicator off after the wait."""
    sun.elevation = 30
    dtu = stuck_dtu()
    dtu.after_reboot = "fresh"
    await setup_entry(hass, dtu_network, dtu)

    await advance(hass, freezer, 100, dtu_network)
    assert len(dtu_network.reboots()) == 1
    assert state(hass, "binary_sensor", "stuck") == STATE_ON

    await advance(hass, freezer, 400, dtu_network)
    assert len(dtu_network.reboots()) == 1
    assert issue(hass, "restart_not_helping") is None
    assert state(hass, "binary_sensor", "stuck") == STATE_OFF


async def test_wait_is_at_least_staleness_time_plus_thirty_seconds(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, sun: Sun
) -> None:
    """A configured wait shorter than the staleness time is lengthened."""
    sun.elevation = 30
    dtu = stuck_dtu()
    await setup_entry(
        hass,
        dtu_network,
        dtu,
        settings={CONF_STALENESS_TIME: 300, CONF_RESTART_WAIT: 60},
    )
    await advance(hass, freezer, 300)
    assert len(dtu_network.reboots()) == 1
    await advance(hass, freezer, 100)
    assert len(dtu_network.reboots()) == 2
