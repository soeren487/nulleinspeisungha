"""Curtailment with an AC Battery, end to end: DTU simulator, fake GX, set time."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, SimDtu, SimGx, SimHouse, setup_entry

OMA, BUERO4 = "114183178036", "116191100801"
METER = "sensor.grid_meter"
POWER = "system/0/Dc/Battery/Power"


def _dtu() -> SimDtu:
    """600 W and 1500 W Inverters, producing 300 W and 700 W."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (300.0, 700.0, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


def _entity(hass: HomeAssistant, platform: str, key: str, house: str = "home") -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"house-{house}_{key}"
    )
    assert entity_id is not None, f"no {platform} {house} {key}"
    return entity_id


def _state(hass: HomeAssistant, platform: str, key: str, house: str = "home") -> str:
    state = hass.states.get(_entity(hass, platform, key, house))
    assert state is not None
    return state.state


def _grid(hass: HomeAssistant, watts: float) -> None:
    hass.states.async_set(METER, str(watts), {"unit_of_measurement": "W"})


def _issue(hass: HomeAssistant, kind: str, house: str = "home") -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_house-{house}")


async def _call(
    hass: HomeAssistant, domain: str, service: str, key: str, **data: object
) -> None:
    await hass.services.async_call(
        domain,
        service,
        {"entity_id": _entity(hass, domain, key), **data},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _curtail(hass: HomeAssistant) -> None:
    await _call(hass, "switch", "turn_on", "curtailment")


async def _set(hass: HomeAssistant, key: str, value: float) -> None:
    await _call(hass, "number", "set_value", key, value=value)


async def _tick(
    hass: HomeAssistant, freezer, gx: SimGx | None, seconds: float = 15
) -> None:
    """Let time pass; the Grid Meter and the GX keep reporting, as they do."""
    freezer.tick(delta=timedelta(seconds=seconds))
    meter = hass.states.get(METER)
    if meter is not None:
        hass.states.async_set(METER, meter.state, meter.attributes)
    if gx is not None and not gx.down:
        gx.set(POWER, float(gx.topics[POWER][9:-1]))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _home(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    gx: SimGx | None,
    grid: float = 0,
    inverters: list[str] | None = None,
) -> None:
    _grid(hass, grid)
    await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[SimHouse("Home", inverters=inverters or [OMA, BUERO4], gx=gx)],
    )


def _limits(dtu_network: DtuNetwork) -> list[tuple[str, int, int]]:
    return sorted(dtu_network.limits())


async def test_room_is_left_for_a_battery_that_can_take_more(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Below full and charging below the maximum, limits rise at the target."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    dtu_network.clear_limits()

    # The battery now has room: 2100 W maximum - 500 W charging - 100 W margin.
    gx.charge(level=80.0, power=500.0)
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"
    # Allowance 504 W + 1500 W = 2004 W of 2100 W.
    assert _limits(dtu_network) == [(OMA, 1, 95), (BUERO4, 1, 95)]
    assert _state(hass, "sensor", "control_state") == "raising"
    assert gx.other_publishes == []


async def test_full_battery_behaves_as_without_a_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A full battery leaves no room: at the target the limits stay."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _curtail(hass)
    dtu_network.clear_limits()
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "holding"
    assert _state(hass, "sensor", "battery_headroom") == "0.0"


async def test_battery_at_maximum_charge_power_behaves_as_without_one(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A battery charging as fast as allowed leaves no room."""
    gx = SimGx()
    gx.charge(level=60.0, power=2100.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    dtu_network.clear_limits()
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    assert dtu_network.limits() == []
    # Lowering the maximum makes the same charge power leave no room either.
    gx.charge(power=500.0)
    await _set(hass, "maximum_charge_power", 600)
    await _tick(hass, freezer, gx)
    assert dtu_network.limits() == []


async def test_bms_limit_narrows_the_headroom(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A BMS that allows only 10 A at 50 V leaves 500 W less the margin."""
    gx = SimGx()
    gx.charge(level=50.0, power=0.0, voltage=50.0, current_limit=10.0)
    await _home(hass, dtu_network, gx, grid=0)
    assert _state(hass, "sensor", "battery_headroom") == "400.0"


async def test_export_beyond_the_band_lowers_and_suppresses_the_headroom(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Two runs in a row exporting despite headroom: lower, and ignore it 5 minutes."""
    gx = SimGx()
    gx.charge(level=80.0, power=500.0)
    await _home(hass, dtu_network, gx, grid=-500)
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"
    await _curtail(hass)
    # The first run gives the battery one Update Interval: nothing is sent.
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "holding"
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"

    # Still exporting although the battery could take 1500 W: lower as usual.
    await _tick(hass, freezer, gx, 15)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    assert _state(hass, "sensor", "battery_headroom") == "0.0"
    dtu_network.clear_limits()

    # At the target again, but for 5 minutes the headroom counts as zero.
    _grid(hass, 0)
    await _tick(hass, freezer, gx, 15)
    await _tick(hass, freezer, gx, 270)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "battery_headroom") == "0.0"

    await _tick(hass, freezer, gx, 20)
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"
    assert _limits(dtu_network) == [(OMA, 1, 95), (BUERO4, 1, 95)]


async def _lowered_then_room(
    hass: HomeAssistant, dtu_network: DtuNetwork, gx: SimGx
) -> None:
    """Limits at 24 % because the battery was full; now it has room to charge."""
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    dtu_network.clear_limits()
    gx.charge(level=80.0, power=500.0)


async def test_one_run_of_export_with_headroom_changes_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A spike while the battery ramps up: nothing is lowered, nothing suppressed."""
    gx = SimGx()
    await _lowered_then_room(hass, dtu_network, gx)
    await _tick(hass, freezer, gx)  # Grid Power still shows the spike
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "holding"
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"
    # The battery has absorbed it: the headroom still counts and raises the limits.
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    assert _limits(dtu_network) == [(OMA, 1, 95), (BUERO4, 1, 95)]


async def test_a_spike_then_a_normal_run_then_a_spike_does_not_suppress(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The memory of a spike is cleared by any run without export."""
    gx = SimGx()
    await _lowered_then_room(hass, dtu_network, gx)
    await _tick(hass, freezer, gx)
    _grid(hass, 0)
    await _tick(hass, freezer, gx)
    dtu_network.clear_limits()
    _grid(hass, -500)
    await _tick(hass, freezer, gx)
    assert dtu_network.limits() == []
    assert _state(hass, "sensor", "control_state") == "holding"
    assert _state(hass, "sensor", "battery_headroom") == "1500.0"


async def test_negative_setpoint_is_capped_by_the_battery_and_raises_an_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Asking for 100 W import with a battery setpoint of 0 W aims for 0 W."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _set(hass, "feed_in_setpoint", -100)
    await _curtail(hass)
    # Aimed at 0 W: 1000 W - 500 W = 500 W of 2100 W. Aimed at 100 W import
    # it would have been 400 W = 19 %.
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    issue = _issue(hass, "feed_in_conflicts_battery_setpoint")
    assert issue is not None
    assert issue.translation_placeholders == {"house": "Home"}
    assert gx.other_publishes == []

    await _set(hass, "feed_in_setpoint", 0)
    await _tick(hass, freezer, gx)
    assert _issue(hass, "feed_in_conflicts_battery_setpoint") is None


async def test_negative_setpoint_within_the_battery_setpoint_is_kept(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A battery that itself imports 150 W allows the 100 W import asked for."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    gx.set("settings/0/Settings/CGwacs/AcPowerSetPoint", 150.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _set(hass, "feed_in_setpoint", -100)
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 19), (BUERO4, 1, 19)]
    assert _issue(hass, "feed_in_conflicts_battery_setpoint") is None


async def test_positive_setpoint_is_never_changed(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Whatever the battery's setpoint, an export Feed-in Setpoint is kept."""
    gx = SimGx()
    gx.charge(level=100.0, power=0.0)
    gx.set("settings/0/Settings/CGwacs/AcPowerSetPoint", -300.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _set(hass, "feed_in_setpoint", 300)
    await _curtail(hass)
    # target -300 W; deviation -200 W; 800 W of 2100 W = 38 %
    assert _limits(dtu_network) == [(OMA, 1, 38), (BUERO4, 1, 38)]
    assert _issue(hass, "feed_in_conflicts_battery_setpoint") is None


async def test_dynamic_ess_raises_an_issue_until_it_is_off(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """An active Dynamic ESS is reported, naming the House; off clears it."""
    gx = SimGx()
    await _home(hass, dtu_network, gx)
    assert _issue(hass, "dynamic_ess_active") is None
    gx.set("settings/0/Settings/DynamicEss/Mode", 1)
    await hass.async_block_till_done()
    issue = _issue(hass, "dynamic_ess_active")
    assert issue is not None
    assert issue.translation_placeholders == {"house": "Home"}
    gx.set("settings/0/Settings/DynamicEss/Mode", 0)
    await hass.async_block_till_done()
    assert _issue(hass, "dynamic_ess_active") is None
    assert gx.other_publishes == []


async def test_silent_battery_raises_an_issue_and_curtailment_goes_on(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """After 2 minutes without the GX there is an issue, and no headroom is counted."""
    gx = SimGx()
    gx.charge(level=80.0, power=500.0)
    await _home(hass, dtu_network, gx, grid=-500)
    await _curtail(hass)
    await _tick(hass, freezer, gx)  # the second run in a row lowers
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    _grid(hass, 0)
    gx.drop()
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", "battery_connected") == "off"
    assert _issue(hass, "battery_not_answering") is None
    await _tick(hass, freezer, gx, 60)
    assert _issue(hass, "battery_not_answering") is None

    # Curtailment goes on without the battery: at the target nothing is raised.
    await _tick(hass, freezer, gx, 30)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    assert _state(hass, "sensor", "control_state") == "holding"
    # ... and import still raises the limits.
    _grid(hass, 400)
    await _tick(hass, freezer, gx, 40)
    assert _issue(hass, "battery_not_answering") is not None
    assert max(v for _, _, v in dtu_network.limits()) == 43
    assert _state(hass, "sensor", "control_state") == "raising"

    gx.restore()
    await hass.async_block_till_done()
    assert _issue(hass, "battery_not_answering") is None
    assert _state(hass, "binary_sensor", "battery_connected") == "on"


async def test_consumption_matches_the_formula(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Grid Power + Inverter production - battery power; unknown without the battery."""
    gx = SimGx()
    gx.charge(level=80.0, power=500.0)
    await _home(hass, dtu_network, gx, grid=300)
    # 300 W import + 1000 W produced - 500 W into the battery
    assert float(_state(hass, "sensor", "consumption")) == 800.0
    gx.charge(power=-400.0)
    assert float(_state(hass, "sensor", "consumption")) == 1700.0
    _grid(hass, -200)
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor", "consumption")) == 1200.0
    gx.drop()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "consumption") == "unavailable"
    gx.restore()
    await hass.async_block_till_done()
    assert float(_state(hass, "sensor", "consumption")) == 1200.0


async def test_consumption_of_a_house_without_a_battery(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """Without an AC Battery its power counts as zero."""
    await _home(hass, dtu_network, None, grid=300)
    assert float(_state(hass, "sensor", "consumption")) == 1300.0


async def test_entities_show_the_recorded_values(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The House device carries the AC Battery's entities with the recorded values."""
    gx = SimGx()
    await _home(hass, dtu_network, gx)
    assert _state(hass, "sensor", "battery_soc") == "82.0"
    assert _state(hass, "sensor", "battery_power") == "-116.0"
    # Discharging counts as zero charge: 2100 W - 100 W margin.
    assert _state(hass, "sensor", "battery_headroom") == "2000.0"
    assert _state(hass, "binary_sensor", "battery_connected") == "on"
    assert float(_state(hass, "number", "maximum_charge_power")) == 2100

    registry = er.async_get(hass)
    expected = {
        ("sensor", "battery_soc"): ("battery", "%", None),
        ("sensor", "battery_power"): ("power", "W", None),
        ("sensor", "battery_headroom"): ("power", "W", "diagnostic"),
        ("binary_sensor", "battery_connected"): ("connectivity", None, None),
        ("number", "maximum_charge_power"): ("power", "W", None),
    }
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "house-home"),
        next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id,
    )
    for (platform, key), (device_class, unit, category) in expected.items():
        entry = registry.async_get(_entity(hass, platform, key))
        assert entry.translation_key == key
        assert entry.device_id == device.id
        assert (entry.original_device_class or entry.device_class) == device_class
        assert entry.entity_category == category
        if unit is not None:
            assert (
                hass.states.get(entry.entity_id).attributes["unit_of_measurement"]
                == unit
            )
    number = hass.states.get(_entity(hass, "number", "maximum_charge_power"))
    assert (number.attributes["min"], number.attributes["max"]) == (100, 20000)
    assert number.attributes["step"] == 50

    gx.charge(level=40.0, power=1000.0)
    assert _state(hass, "sensor", "battery_soc") == "40.0"
    assert _state(hass, "sensor", "battery_headroom") == "1000.0"
    await _set(hass, "maximum_charge_power", 3000)
    assert _state(hass, "sensor", "battery_headroom") == "1900.0"


async def test_entities_are_unavailable_while_the_battery_is_silent(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """After losing the GX the battery sensors have no value."""
    gx = SimGx()
    await _home(hass, dtu_network, gx)
    gx.drop()
    await hass.async_block_till_done()
    for key in ("battery_soc", "battery_power", "battery_headroom"):
        assert _state(hass, "sensor", key) == "unavailable"
    assert _state(hass, "binary_sensor", "battery_connected") == "off"


async def test_maximum_charge_power_is_restored(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """The owner's Maximum Charge Power survives a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.home_maximum_charge_power", "4200.0"),
                {
                    "native_max_value": 20000,
                    "native_min_value": 100,
                    "native_step": 50,
                    "native_unit_of_measurement": "W",
                    "native_value": 4200.0,
                },
            )
        ],
    )
    await _home(hass, dtu_network, SimGx())
    assert float(_state(hass, "number", "maximum_charge_power")) == 4200


async def test_house_without_battery_has_no_battery_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """No battery entities, no issues, and Curtailment as before."""
    entry_gx = None
    await _home(hass, dtu_network, entry_gx, grid=-500)
    registry = er.async_get(hass)
    for platform, key in (
        ("sensor", "battery_soc"),
        ("sensor", "battery_power"),
        ("sensor", "battery_headroom"),
        ("binary_sensor", "battery_connected"),
        ("number", "maximum_charge_power"),
    ):
        assert (
            registry.async_get_entity_id(platform, DOMAIN, f"house-home_{key}") is None
        )
    assert registry.async_get_entity_id("sensor", DOMAIN, "house-home_consumption")
    await _curtail(hass)
    assert _limits(dtu_network) == [(OMA, 1, 24), (BUERO4, 1, 24)]
    dtu_network.clear_limits()
    _grid(hass, 0)
    await _tick(hass, freezer, None)
    assert dtu_network.limits() == []
    assert not ir.async_get(hass).issues
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.state is ConfigEntryState.LOADED


async def test_unload_closes_the_connection(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Unloading the entry disconnects from the GX and stops the keepalive."""
    gx = SimGx()
    await _home(hass, dtu_network, gx)
    (client,) = gx.clients
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert client.closed
    assert gx.clients == []
    sent = len(gx.published)
    await _tick(hass, freezer, gx, 60)
    assert len(gx.published) == sent
