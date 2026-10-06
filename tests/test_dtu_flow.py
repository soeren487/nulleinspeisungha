"""Tests for the DTU subentry flow (adding/reconfiguring DTUs)."""

from __future__ import annotations

from aiohttp.client_exceptions import ClientConnectionError
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nulleinspeisung.const import (
    CONF_PASSWORD,
    CONF_URL,
    DOMAIN,
    SUBENTRY_TYPE_DTU,
)
from tests.conftest import DtuNetwork, SimDtu


async def test_add_dtu_with_valid_address_and_password(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Adding a DTU creates a subentry titled with hostname."""
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.50"
    dtu_network.add(dtu)
    dtu_network.apply()

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "test_password",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "OpenDTU-Buero"

    subentry_id = result.get("subentry_id") or (
        list(entry.subentries.keys())[-1] if entry.subentries else None
    )
    assert subentry_id is not None

    subentry = entry.subentries[subentry_id]
    assert subentry.unique_id == "199980126212"
    assert subentry.data[CONF_URL] == "http://192.168.1.50"
    assert subentry.data[CONF_PASSWORD] == "test_password"


async def test_dtu_unreachable_shows_cannot_connect_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A DTU that cannot be reached shows form error cannot_connect."""
    aioclient_mock.get(
        "http://unreachable.local/api/dtu/config",
        exc=ClientConnectionError(),
    )

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "unreachable.local",
            CONF_PASSWORD: "wrong",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_form_reappears_after_cannot_connect_can_succeed(
    hass: HomeAssistant, dtu_network: DtuNetwork, aioclient_mock
) -> None:
    """After cannot_connect error, the form can be completed successfully."""
    # First request fails
    aioclient_mock.get(
        "http://unreachable.local/api/dtu/config",
        exc=ClientConnectionError(),
    )

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "unreachable.local",
            CONF_PASSWORD: "wrong",
        },
    )
    assert result["errors"]["base"] == "cannot_connect"

    # Now set up a valid DTU at a different address
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.50"
    dtu_network.add(dtu)
    dtu_network.apply()

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "correct",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_wrong_password_shows_invalid_auth_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """A wrong password (HTTP 401) shows form error invalid_auth."""
    aioclient_mock.get(
        "http://192.168.1.50/api/dtu/config",
        status=401,
    )

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "wrong_password",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def test_adding_same_dtu_twice_aborts_already_configured(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Adding the same DTU twice aborts with already_configured."""
    dtu = SimDtu.default()
    dtu_network.add(dtu)
    dtu_network.apply()

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    # Add first DTU
    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "http://opendtu.local",
            CONF_PASSWORD: "password",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Try to add the same DTU again
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "http://opendtu.local",
            CONF_PASSWORD: "password",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_two_different_dtus_can_be_added(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Two different DTUs can be added to the same config entry."""
    dtu1 = SimDtu.default()
    dtu1.base_url = "http://192.168.1.50"
    dtu1.serial = "111111111111"
    dtu1.hostname = "DTU-Office"
    dtu_network.add(dtu1)

    dtu2 = SimDtu.default()
    dtu2.base_url = "http://192.168.1.51"
    dtu2.serial = "222222222222"
    dtu2.hostname = "DTU-Garden"
    dtu_network.add(dtu2)

    dtu_network.apply()

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    # Add first DTU
    result: ConfigFlowResult = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "password1",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Add second DTU at different address
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.51",
            CONF_PASSWORD: "password2",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "DTU-Garden"

    # Verify both are added
    assert len(entry.subentries) == 2


async def test_reconfigure_changes_address_and_password(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Reconfigure changes the DTU address and password."""
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.50"
    dtu_network.add(dtu)
    dtu_network.apply()

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    # Add DTU
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "password1",
        },
    )
    subentry_id = result.get("subentry_id") or (
        list(entry.subentries.keys())[-1] if entry.subentries else None
    )
    assert subentry_id is not None

    # Set up new DTU at different address with same serial
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.60"
    dtu_network.add(dtu)
    dtu_network.apply()

    # Reconfigure
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.60",
            CONF_PASSWORD: "password2",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    subentry = entry.subentries[subentry_id]
    assert subentry.data[CONF_URL] == "http://192.168.1.60"
    assert subentry.data[CONF_PASSWORD] == "password2"


async def test_reconfigure_different_dtu_serial_aborts_wrong_dtu(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Reconfiguring to a different DTU serial aborts with wrong_dtu."""
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.50"
    dtu_network.add(dtu)
    dtu_network.apply()

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    # Add DTU
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU),
        context={"source": "user"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.50",
            CONF_PASSWORD: "password",
        },
    )
    subentry_id = result.get("subentry_id") or (
        list(entry.subentries.keys())[-1] if entry.subentries else None
    )
    assert subentry_id is not None

    # Set up a different DTU
    dtu = SimDtu.default()
    dtu.base_url = "http://192.168.1.60"
    dtu.serial = "999999999999"  # Different serial
    dtu_network.add(dtu)
    dtu_network.apply()

    # Try to reconfigure
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_URL: "192.168.1.60",
            CONF_PASSWORD: "password",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_dtu"


# --- Two DTUs with the same serial (ticket 24) ---

from custom_components.nulleinspeisung.const import CONF_MAC  # noqa: E402

_FIRST = "http://192.168.1.50"
_SECOND = "http://192.168.1.51"


def _sim(url: str, mac: str, hostname: str = "OpenDTU-Buero") -> SimDtu:
    dtu = SimDtu.default()
    dtu.base_url = url
    dtu.mac = mac
    dtu.hostname = hostname
    return dtu


async def _add(hass: HomeAssistant, entry: MockConfigEntry, address: str):
    """Run the add form for ``address`` and return the result."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_DTU), context={"source": "user"}
    )
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_URL: address, CONF_PASSWORD: "pw"}
    )


async def test_same_serial_other_device_shows_duplicate_serial(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A different device with the same serial is told apart, and can be added
    after the owner gave it another serial."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01", "DTU-Office"))
    second = _sim(_SECOND, "AA:BB:CC:00:00:09", "DTU-Garden")
    dtu_network.add(second)
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert (await _add(hass, entry, "192.168.1.50"))["type"] is (
        FlowResultType.CREATE_ENTRY
    )

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "duplicate_serial"}
    assert result["description_placeholders"]["existing"] == "DTU-Office"

    second.serial = "222222222222"
    dtu_network.apply()
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.51", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 2


async def test_same_device_twice_is_already_configured_by_mac(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The same hardware address under another address is the same DTU."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01"))
    dtu_network.add(_sim(_SECOND, "aa-bb-cc-00-00-01"))
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await _add(hass, entry, "192.168.1.50")

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _released_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry whose DTU subentry has no stored hardware address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {CONF_URL: _FIRST, CONF_PASSWORD: "oldpw"},
                "subentry_type": SUBENTRY_TYPE_DTU,
                "title": "DTU-Office",
                "unique_id": "199980126212",
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def test_existing_without_mac_is_asked_and_differs(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A subentry from the released version is asked for its hardware address."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01", "DTU-Office"))
    dtu_network.add(_sim(_SECOND, "AA:BB:CC:00:00:09", "DTU-Garden"))
    dtu_network.apply()
    entry = await _released_entry(hass)

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "duplicate_serial"}
    assert result["description_placeholders"]["existing"] == "DTU-Office"
    assert CONF_MAC not in next(iter(entry.subentries.values())).data


async def test_existing_without_mac_same_device_is_already_configured(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Asking the existing DTU finds the same hardware address."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01"))
    dtu_network.add(_sim(_SECOND, "AA:BB:CC:00:00:01"))
    dtu_network.apply()
    entry = await _released_entry(hass)

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_existing_not_answering_is_already_configured(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """If the existing DTU cannot be asked, the old answer stands."""
    existing = _sim(_FIRST, "AA:BB:CC:00:00:01")
    existing.down = True
    dtu_network.add(existing)
    dtu_network.add(_sim(_SECOND, "AA:BB:CC:00:00:09"))
    dtu_network.apply()
    entry = await _released_entry(hass)

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_new_dtu_without_mac_is_already_configured(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Without the new DTU's hardware address nothing can be told apart."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01"))
    second = _sim(_SECOND, "AA:BB:CC:00:00:09")
    second.network_fails = True
    dtu_network.add(second)
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await _add(hass, entry, "192.168.1.50")

    result = await _add(hass, entry, "192.168.1.51")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_added_and_reconfigured_dtu_store_mac(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The hardware address is stored on add and on reconfigure."""
    dtu_network.add(_sim(_FIRST, "AA:BB:CC:00:00:01"))
    dtu_network.apply()
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    result = await _add(hass, entry, "192.168.1.50")
    subentry_id = next(iter(entry.subentries))
    assert entry.subentries[subentry_id].data[CONF_MAC] == "aabbcc000001"

    # Same DTU, now reached under another address and with another interface.
    dtu_network.dtus.clear()
    dtu_network.add(_sim(_SECOND, "AA:BB:CC:00:00:77"))
    dtu_network.apply()
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.51", CONF_PASSWORD: "pw"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data[CONF_MAC] == "aabbcc000077"
