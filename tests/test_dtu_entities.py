"""Tests for DTU entities (Inverters, sensors, binary sensors)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
)

from custom_components.nulleinspeisung.const import (
    DOMAIN,
    DTU_UPDATE_INTERVAL,
)
from tests.conftest import DtuNetwork, SimDtu, SimInverter, setup_entry


async def test_inverter_devices_created_after_setup(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Each Inverter is a device under the DTU device (via_device_id)."""
    dtu = SimDtu.default()
    entry = await setup_entry(hass, dtu_network, dtu)

    assert entry.state == ConfigEntryState.LOADED

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)

    # Check DTU device exists
    dtu_device = registry.async_get_device_by_identifier(
        (DOMAIN, "199980126212"), entry.entry_id
    )
    assert dtu_device is not None
    assert dtu_device.name == "OpenDTU-Buero"
    assert dtu_device.manufacturer == "OpenDTU"

    # Check Inverter devices exist
    for serial in ["114183178036", "116191100801", "1164a00ccd81"]:
        device = registry.async_get_device_by_identifier(
            (DOMAIN, serial), entry.entry_id
        )
        assert device is not None
        assert device.manufacturer == "Hoymiles"
        assert device.serial_number == serial
        assert device.via_device_id == dtu_device.id


async def test_dtu_device_name_and_firmware(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The DTU device has the hostname as name and firmware as sw_version."""
    dtu = SimDtu.default()
    entry = await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    dtu_device = registry.async_get_device_by_identifier(
        (DOMAIN, "199980126212"), entry.entry_id
    )

    assert dtu_device.name == "OpenDTU-Buero"
    assert dtu_device.sw_version == "v26.3.30"


async def test_inverter_entity_states(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Inverter entities show state values from simulator."""
    dtu = SimDtu.default()
    # Customize one inverter
    dtu.inverters[1] = SimInverter(
        serial="116191100801",
        name="Büro 4",
        reachable=True,
        producing=True,
        poll_enabled=True,
        data_age=7,
        power=412.5,
        limit=80.0,
        rated_power=1500,
        model="HM-1500-4T",
    )
    await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)

    reachable_ent = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, "116191100801_reachable"
    )
    producing_ent = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, "116191100801_producing"
    )
    power_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "116191100801_power")
    limit_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "116191100801_limit")
    data_age_ent = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "116191100801_data_age"
    )
    rated_power_ent = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "116191100801_rated_power"
    )

    assert hass.states.get(reachable_ent).state == "on"
    assert hass.states.get(producing_ent).state == "on"
    assert float(hass.states.get(power_ent).state) == 412.5
    assert float(hass.states.get(limit_ent).state) == 80.0
    assert hass.states.get(data_age_ent).state == "7"
    assert float(hass.states.get(rated_power_ent).state) == 1500.0


async def test_values_refresh_on_interval(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Entity values refresh after DTU_UPDATE_INTERVAL."""
    dtu = SimDtu.default()
    await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    power_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "116191100801_power")

    assert float(hass.states.get(power_ent).state) == 0.0

    # Change simulator state
    dtu = SimDtu.default()
    dtu.inverters[1].power = 500.0
    dtu_network.add(dtu)
    dtu_network.apply()

    # Advance time
    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert float(hass.states.get(power_ent).state) == 500.0


async def test_entities_unavailable_when_dtu_down(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Entities become unavailable when DTU is down; recover when it comes up."""
    dtu = SimDtu.default()
    await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    power_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "116191100801_power")

    assert hass.states.get(power_ent).state != STATE_UNAVAILABLE

    # Take DTU down
    dtu = SimDtu.default()
    dtu.down = True
    dtu_network.add(dtu)
    dtu_network.apply()

    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(power_ent).state == STATE_UNAVAILABLE

    # Bring DTU back up
    dtu = SimDtu.default()
    dtu_network.add(dtu)
    dtu_network.apply()

    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(power_ent).state != STATE_UNAVAILABLE


async def test_dtu_down_at_start_does_not_block_setup(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A down DTU at startup does not prevent setup (entry LOADED)."""
    dtu = SimDtu.default()
    dtu.down = True
    entry = await setup_entry(hass, dtu_network, dtu)

    assert entry.state == ConfigEntryState.LOADED


async def test_second_dtu_works_while_first_down(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Second DTU works meanwhile while first DTU is down."""
    # Add down DTU
    dtu1 = SimDtu.default()
    dtu1.down = True
    dtu1.serial = "111111111111"
    dtu1.base_url = "http://down.local"

    # Add working DTU
    dtu2 = SimDtu.default()
    dtu2.serial = "199980126212"

    entry = await setup_entry(hass, dtu_network, dtu1, dtu2)

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    # Second DTU should have entities
    power_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "116191100801_power")
    assert power_ent is not None
    assert hass.states.get(power_ent).state != STATE_UNAVAILABLE

    # Bring first DTU up
    dtu1.down = False
    dtu_network.add(dtu1)
    dtu_network.apply()

    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # First DTU should now have devices
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    dtu1_device = registry.async_get_device_by_identifier(
        (DOMAIN, "111111111111"), entry.entry_id
    )
    assert dtu1_device is not None


async def test_empty_detail_gives_unknown_power(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Inverter with detail_empty=True has power state unknown."""
    dtu = SimDtu.default()
    dtu.inverters[0].detail_empty = True
    await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)

    # OmaOpa should have unknown power
    power_ent = ent_reg.async_get_entity_id("sensor", DOMAIN, "114183178036_power")
    assert hass.states.get(power_ent).state == "unknown"

    # Other inverter should have value
    power_ent_other = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, "116191100801_power"
    )
    assert hass.states.get(power_ent_other).state != "unknown"


async def test_devinfo_invalid_then_valid(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Inverter model is set when devinfo becomes valid without reload."""
    dtu = SimDtu.default()
    dtu.inverters[1].devinfo_valid = False
    dtu.inverters[1].model = None
    entry = await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, "116191100801"), entry.entry_id
    )
    assert device.model is None or device.model == ""

    # Make devinfo valid
    dtu = SimDtu.default()
    dtu_network.add(dtu)
    dtu_network.apply()

    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    device = registry.async_get_device_by_identifier(
        (DOMAIN, "116191100801"), entry.entry_id
    )
    assert device.model == "HM-1500-4T"


async def test_new_inverter_gets_entities_without_reload(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """New Inverter in simulator gets device and entities without reload."""
    dtu = SimDtu.default()
    dtu.inverters = dtu.inverters[:2]  # Only 2 inverters
    entry = await setup_entry(hass, dtu_network, dtu)

    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Third inverter should not exist yet
    device_3 = dev_reg.async_get_device_by_identifier(
        (DOMAIN, "1164a00ccd81"), entry.entry_id
    )
    assert device_3 is None

    # Add third inverter
    dtu = SimDtu.default()
    dtu_network.add(dtu)
    dtu_network.apply()

    freezer.tick(delta=DTU_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Third inverter should now exist
    device_3 = dev_reg.async_get_device_by_identifier(
        (DOMAIN, "1164a00ccd81"), entry.entry_id
    )
    assert device_3 is not None

    power_ent_3 = ent_reg.async_get_entity_id("sensor", DOMAIN, "1164a00ccd81_power")
    assert power_ent_3 is not None


async def test_only_get_requests_sent(
    hass: HomeAssistant, dtu_network: DtuNetwork, aioclient_mock
) -> None:
    """Only GET requests are ever sent to the DTU."""
    dtu = SimDtu.default()
    await setup_entry(hass, dtu_network, dtu)

    # All calls should be GET
    assert len(aioclient_mock.mock_calls) > 0
    for call in aioclient_mock.mock_calls:
        assert call[0] == "GET", f"Non-GET request: {call[0]}"
