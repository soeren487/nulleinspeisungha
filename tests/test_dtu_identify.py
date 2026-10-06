"""Tests for identifying a DTU, including its hardware address."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.nulleinspeisung.dtu_client import DtuClient
from tests.conftest import DtuNetwork, SimDtu


async def test_identify_returns_normalised_hardware_address(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """``AA:BB:CC:00:00:01`` becomes ``aabbcc000001``."""
    dtu_network.add(SimDtu.default())
    dtu_network.apply()
    client = DtuClient(async_get_clientsession(hass), "opendtu.local", "pw")
    identity = await client.async_identify()
    assert identity.mac == "aabbcc000001"
    assert identity.serial == "199980126212"


async def test_identify_survives_failing_network_status(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A failing network status gives no hardware address, not a failure."""
    dtu = SimDtu.default()
    dtu.network_fails = True
    dtu_network.add(dtu)
    dtu_network.apply()
    client = DtuClient(async_get_clientsession(hass), "opendtu.local", "pw")
    identity = await client.async_identify()
    assert identity.mac is None
    assert identity.hostname == "OpenDTU-Buero"
