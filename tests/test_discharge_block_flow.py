"""The Discharge Block end to end: simulated Tibber, GX, DTU and a set clock.

The world is that of ``test_grid_charging_flow``: a House in Berlin, 22:00 on 4
October 2026 (UTC+2), 10 kWh battery, qualifying levels cheap and very cheap.

A Discharge Block is on in a cheap quarter-hour only when the Charging Plan holds
the battery there: the energy is needed later at a higher price and would be
missing there. In the first tests the rest of the day is normal-priced and the
10 kWh a day of the Expected Load is more than the battery carries, so the cheap
quarter-hours are held; the last tests show when they are not.
"""

from __future__ import annotations

import json
from datetime import UTC
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from tests.conftest import DtuNetwork, SimGx, SimTibber
from tests.test_grid_charging_flow import (
    CHEAP_PAIR,
    DYNAMIC_ESS,
    NIGHT_PRICES,
    _call,
    _house,
    _override,
    _settle,
    _setup,
    _value,
    _writes,
)

MAX_DISCHARGE = "hub4/0/Overrides/MaxDischargePower"
TWO_CHEAP = {"02:00": (0.30, "CHEAP"), "02:15": (0.30, "CHEAP")}
ALL_DAY_CHEAP = {
    f"{h:02d}:{m:02d}": (0.30, "CHEAP") for h in range(24) for m in (0, 15, 30, 45)
}
"""The whole 5 October is cheap: no dearer quarter is left to keep energy for."""
CHEAP_NIGHT = {
    f"{h:02d}:{m:02d}": (0.30, "CHEAP") for h in range(2, 6) for m in (0, 15, 30, 45)
}
"""Cheap from 02:00 to 06:00, then normal at 0.40: the day needs the battery."""


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


def _discharge(gx: SimGx) -> list[float | None]:
    """The values written to the discharge override, in order."""
    return [
        json.loads(payload)["value"]
        for topic, payload in gx.other_publishes
        if topic.endswith(MAX_DISCHARGE)
    ]


def _on_gx(gx: SimGx) -> float | None:
    return json.loads(gx.topics[MAX_DISCHARGE])["value"]


def _block(hass: HomeAssistant) -> str:
    return _value(hass, "binary_sensor", "discharge_block")


async def _blocking(hass, dtu_network, tibber, freezer, **kw):
    """Set up inside a cheap quarter-hour with a full battery: block, no charging."""
    kw.setdefault("level", 100.0)
    kw.setdefault("prices", TWO_CHEAP)
    entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, at="2026-10-05T02:05", **kw
    )
    return entry, gx


async def test_the_block_follows_the_quarter_hours(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """On at the boundary where the cheap level starts (the battery cannot carry
    the dear rest of the day), off where it stops."""
    entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, level=100.0, prices=TWO_CHEAP
    )
    assert _block(hass) == "off"
    assert _house(entry).wanted_overrides.max_discharge_power is None
    assert _discharge(gx) == []

    await _settle(hass, freezer, gx, to="2026-10-05T01:59:59")
    assert _block(hass) == "off"
    assert _discharge(gx) == []

    await _settle(hass, freezer, gx, to="2026-10-05T02:00")
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]
    assert _on_gx(gx) == 0.0
    assert _house(entry).wanted_overrides.max_discharge_power == 0.0

    # The second cheap quarter-hour continues the block: no further write.
    await _settle(hass, freezer, gx, to="2026-10-05T02:14:59")
    assert _block(hass) == "on"
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    assert _block(hass) == "on"
    await _settle(hass, freezer, gx, seconds=120)
    assert _discharge(gx) == [0.0]

    await _settle(hass, freezer, gx, to="2026-10-05T02:30")
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
    assert _on_gx(gx) is None
    await _settle(hass, freezer, gx, seconds=120)
    await _settle(hass, freezer, gx, to="2026-10-05T03:00")
    assert _discharge(gx) == [0.0, -1]
    assert _writes(gx) == []


async def test_the_block_is_active_where_no_charging_is_planned(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The battery is at its target: nothing is charged, the plan still holds it."""
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    assert _value(hass, "sensor", "grid_charging_state") == "target_reached"
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]
    assert _writes(gx) == []


async def test_the_block_does_not_depend_on_the_efficiency(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Cheap by level but dearer than 78 % of the reference: charging is blocked,
    the battery is held all the same (no conversion loss when it is not charged)."""
    prices = {f"02:{m:02d}": (0.38, "CHEAP") for m in (0, 15, 30, 45)}
    _entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, at="2026-10-05T02:05", prices=prices
    )
    assert _value(hass, "sensor", "grid_charging_state") == "blocked_by_efficiency"
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]
    assert _writes(gx) == []


async def test_a_charging_slot_has_both_overrides(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _setup(
        hass, dtu_network, tibber, freezer, at="2026-10-05T02:05", prices=NIGHT_PRICES
    )
    assert _value(hass, "sensor", "grid_charging_state") == "charging"
    assert _block(hass) == "on"
    assert _writes(gx) == [2200.0]
    assert _discharge(gx) == [0.0]
    wanted = _house(entry).wanted_overrides
    assert (wanted.setpoint, wanted.max_discharge_power) == (2200.0, 0.0)

    # 02:30 is normal: both go.
    await _settle(hass, freezer, gx, to="2026-10-05T02:30")
    assert _block(hass) == "off"
    assert _writes(gx) == [2200.0, None]
    assert _discharge(gx) == [0.0, -1]
    assert _override(gx) is None
    assert _on_gx(gx) is None


async def test_the_block_stays_while_charging_ends_inside_it(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The battery gets full during a cheap quarter-hour: the setpoint goes only.

    Only two adjacent cheap quarter-hours exist: with more of them later, the
    plan would wait for one after the battery has been used a little.
    """
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        level=99.0,
        prices=CHEAP_PAIR,
    )
    assert _writes(gx) == [2200.0]
    gx.charge(level=100.0)
    await hass.async_block_till_done()
    assert _value(hass, "sensor", "grid_charging_state") == "target_reached"
    assert _writes(gx) == [2200.0, None]
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]


# -- what lifts the block at once ------------------------------------------


async def test_switching_grid_charging_off_lifts_the_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    await _call(hass, "switch", "turn_off", "grid_charging")
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
    assert _on_gx(gx) is None
    await _settle(hass, freezer, gx, seconds=60)
    assert _discharge(gx) == [0.0, -1]
    await _call(hass, "switch", "turn_on", "grid_charging")
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0, -1, 0.0]


async def test_losing_the_prices_lifts_the_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    tibber.down = True
    _house(entry).prices.prices = ()
    await _settle(hass, freezer, gx, to="2026-10-05T02:10")
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
    assert _house(entry).wanted_overrides.max_discharge_power is None


async def test_a_stale_battery_lifts_the_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    gx.dead = True
    await _settle(hass, freezer, gx, seconds=90)
    await _settle(hass, freezer, gx, seconds=15)
    assert _block(hass) == "off"
    assert _house(entry).wanted_overrides.max_discharge_power is None
    assert _discharge(gx)[-1] == -1


async def test_dynamic_ess_lifts_the_block_and_its_end_brings_it_back(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    gx.set(DYNAMIC_ESS, 1)
    await hass.async_block_till_done()
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
    gx.set(DYNAMIC_ESS, 0)
    await hass.async_block_till_done()
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0, -1, 0.0]


async def test_switching_off_the_hand_over_lifts_the_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    await _call(hass, "switch", "turn_off", "publish_grid_power")
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
    assert _on_gx(gx) is None
    # From here on nothing is written, whatever happens on the GX.
    gx.set(MAX_DISCHARGE, 500)
    await _settle(hass, freezer, gx, seconds=60)
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    assert _discharge(gx) == [0.0, -1]
    assert _on_gx(gx) == 500


# -- when there is never a block -------------------------------------------


async def test_with_grid_charging_off_there_is_never_a_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(
        hass, dtu_network, tibber, freezer, switches={"grid_charging": "off"}
    )
    assert _block(hass) == "off"
    await _settle(hass, freezer, gx, to="2026-10-05T02:15")
    await _settle(hass, freezer, gx, seconds=60)
    assert _block(hass) == "off"
    assert gx.other_publishes == []


async def test_without_the_hand_over_there_is_never_a_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(
        hass, dtu_network, tibber, freezer, switches={"publish_grid_power": "off"}
    )
    await _settle(hass, freezer, gx, seconds=30)
    assert _block(hass) == "off"
    assert gx.other_publishes == []


async def test_a_never_stored_switch_means_no_block_and_no_write(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(
        hass, dtu_network, tibber, freezer, switches={"grid_charging": None}
    )
    assert _value(hass, "switch", "grid_charging") == "off"
    assert _block(hass) == "off"
    assert gx.other_publishes == []


async def test_a_restart_inside_a_block_writes_once_after_restoring(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer)
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]
    await _settle(hass, freezer, gx, seconds=60)
    assert _discharge(gx) == [0.0]


async def test_a_gx_that_does_not_confirm_is_written_again_later(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    gx = SimGx()
    gx.accepts_writes = False
    _entry, gx = await _blocking(hass, dtu_network, tibber, freezer, gx=gx)
    assert _discharge(gx) == [0.0]
    await _settle(hass, freezer, gx, seconds=5)
    assert _discharge(gx) == [0.0]
    await _settle(hass, freezer, gx, seconds=10)
    assert _discharge(gx)[:2] == [0.0, 0.0]


# -- Curtailment -----------------------------------------------------------


async def test_curtailment_keeps_working_during_a_block(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The battery holds back, the House imports: the limits go to 100 %."""
    _entry, gx = await _blocking(
        hass,
        dtu_network,
        tibber,
        freezer,
        grid=300.0,
        production=(200.0, 0.0),
        switches={"curtailment": "on"},
    )
    assert _block(hass) == "on"
    for _ in range(6):
        await _settle(hass, freezer, gx, seconds=15)
    limits = dtu_network.limits()
    assert {percent for _, _, percent in limits} <= {100}
    assert _value(hass, "sensor", "control_state") in ("holding", "raising")
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]


# -- the block is on only where the stored energy is needed later (ticket 37) ----


async def test_a_long_cheap_period_does_not_block_the_battery(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """Cheap by level for the whole known time and the battery at 95 %: no dearer
    quarter-hour wants its energy, so it discharges as usual."""
    entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        level=95.0,
        prices=ALL_DAY_CHEAP,
    )
    assert _value(hass, "sensor", "grid_charging_state") == "no_shortfall"
    assert _block(hass) == "off"
    assert _house(entry).grid_charging.plan.held == ()

    for moment in ("2026-10-05T02:15", "2026-10-05T03:00", "2026-10-05T12:00"):
        await _settle(hass, freezer, gx, to=moment)
        assert _block(hass) == "off", moment
    assert _discharge(gx) == []
    assert _writes(gx) == []


async def test_before_a_dearer_day_the_latest_cheap_quarters_are_blocked(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The battery cannot carry the day after the cheap night: it discharges at
    first and is held in the last cheap quarter-hours, as far as it needs."""
    entry, _gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T02:05",
        level=100.0,
        prices=CHEAP_NIGHT,
        daily_kwh=8.5,
    )
    assert _block(hass) == "off"
    held = _house(entry).grid_charging.plan.held
    assert held
    # The cheap quarters are 02:00 to 06:00 local = 00:00 to 04:00 UTC. The
    # battery carries 7.8 of the 15.7 quarters left (spare content 0.78 kWh above
    # the minimum, 0.1 kWh each), so the last 8 are held: 04:00 to 06:00 local.
    assert [h.astimezone(UTC).strftime("%H:%M") for h in held] == [
        f"0{h}:{m:02d}" for h in (2, 3) for m in (0, 15, 30, 45)
    ]
    assert _house(entry).wanted_overrides.max_discharge_power is None


async def test_the_block_holds_the_last_cheap_quarters_and_ends_with_them(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    """The same night at 04:05, when the battery has used up its spare content
    (7.7 quarters of 0.1 kWh): it is held to the end of the cheap quarters."""
    _entry, gx = await _setup(
        hass,
        dtu_network,
        tibber,
        freezer,
        at="2026-10-05T04:05",
        level=92.3,
        prices=CHEAP_NIGHT,
        daily_kwh=8.5,
    )
    assert _block(hass) == "on"
    assert _discharge(gx) == [0.0]
    await _settle(hass, freezer, gx, to="2026-10-05T06:00")
    assert _block(hass) == "off"
    assert _discharge(gx) == [0.0, -1]
