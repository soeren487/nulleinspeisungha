"""Curtailment against Inverters that ramp as measured, with controlled time."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import DtuNetwork, RampSimulator, SimDtu, SimHouse, setup_entry
from tests.test_curtailment_flow import (
    BUERO4,
    OMA,
    _curtail,
    _dtu,
    _grid,
    _set,
    _state,
)

pytestmark = pytest.mark.measured_inverters

BAND = 30.0
STEP = 5


@dataclass
class Plant:
    """A House with ramping Inverters and a load, advanced in steps of 5 s."""

    hass: HomeAssistant
    freezer: object
    network: DtuNetwork
    sim: RampSimulator
    dtu: SimDtu
    load: float
    coordinators: list = field(default_factory=list)
    """Polled after every step: the phase of the coordinators' own timers depends
    on the real clock, which would make the readings' age vary from run to run."""
    trace: list[tuple[float, float, float]] = field(default_factory=list)
    """(time, Grid Power, production) after every step."""

    def production(self) -> float:
        return sum(i.power or 0.0 for i in self.dtu.inverters[:2])

    def feed_grid(self) -> None:
        _grid(self.hass, round(self.load - self.production(), 1))

    async def run(self, seconds: float) -> None:
        for _ in range(int(seconds / STEP)):
            self.freezer.tick(delta=timedelta(seconds=STEP))
            self.sim.advance(STEP)
            self.feed_grid()
            for coordinator in self.coordinators:
                await coordinator.async_refresh()
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            self.trace.append(
                (self.sim.time, self.load - self.production(), self.production())
            )

    def commands(self, serial: str) -> int:
        return sum(1 for s, _, _ in self.network.limits() if s == serial)

    def since(self, start: float) -> list[tuple[float, float, float]]:
        return [row for row in self.trace if row[0] > start]

    def back_in_band(self, start: float) -> float:
        """Seconds after ``start`` from which Grid Power stays within the band."""
        late = [t for t, grid, _ in self.since(start) if abs(grid) > BAND]
        return (max(late) + STEP if late else 0.0) - start

    def worst_import(self, start: float) -> float:
        return max(grid for _, grid, _ in self.since(start))

    def worst_export(self, start: float) -> float:
        return -min(grid for _, grid, _ in self.since(start))


async def _plant(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    load: float = 630.0,
    available: tuple[float, float] = (180.0, 450.0),
    reserve: float = 0.0,
    battery_backed: list[str] | None = None,
) -> Plant:
    dtu = _dtu()
    for inverter, power in zip(dtu.inverters, available, strict=False):
        inverter.available = power
        inverter.power = power
        inverter.data_age = 2
    dtu.inverters[2].reachable = False
    plant_power = sum(available)
    _grid(hass, round(load - plant_power, 1))
    entry = await setup_entry(
        hass,
        dtu_network,
        dtu,
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                battery_backed=battery_backed or [],
            )
        ],
    )
    sim = RampSimulator(dtu_network)
    plant = Plant(hass, freezer, dtu_network, sim, dtu, load)
    plant.coordinators = list(entry.runtime_data.dtus.values())
    if reserve:
        await _set(hass, "response_reserve", reserve)
    await _curtail(hass)
    return plant


def _entity_state(hass: HomeAssistant, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"house-home_{key}"
    )
    assert entity_id is not None
    return hass.states.get(entity_id), er.async_get(hass).async_get(entity_id)


async def test_the_new_settings_are_config_numbers_with_defaults(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    await _plant(hass, dtu_network, freezer)
    slew, slew_entry = _entity_state(hass, "limit_slew_rate")
    reserve, reserve_entry = _entity_state(hass, "response_reserve")
    assert float(slew.state) == 0.5
    assert (slew.attributes["min"], slew.attributes["max"]) == (0.1, 5)
    assert slew.attributes["step"] == 0.1
    assert float(reserve.state) == 0
    assert (reserve.attributes["min"], reserve.attributes["max"]) == (0, 50)
    assert reserve.attributes["step"] == 1
    assert slew_entry.entity_category == reserve_entry.entity_category == "config"


async def test_load_drop_from_100_is_lowered_once_and_without_overshoot(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Export of 400 W with the Inverters at 100 % while producing 30 %.

    Measured with this simulator: one lowering per Inverter (11 %), nothing
    more while it is on its way, back in the band after 195 s, no import at
    all; the export is gone only when the effective limit has crossed the
    output (140 s) and then run down.
    """
    plant = await _plant(hass, dtu_network, freezer)
    await plant.run(30)
    start = plant.sim.time
    plant.load = 230.0
    await plant.run(100)
    for serial in (OMA, BUERO4):
        assert plant.commands(serial) == 1
    await plant.run(200)
    assert [v for _, _, v in plant.network.limits()] == [11, 11]
    for serial in (OMA, BUERO4):
        assert plant.commands(serial) == 1
    assert 140 <= plant.back_in_band(start) <= 200
    assert plant.worst_import(start) <= BAND
    assert _state(hass, "sensor", "control_state") == "holding"


async def test_load_drop_with_a_reserve_is_corrected_several_times_faster(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The limits sit 10 points above the output: back in the band after 70 s.

    Without the reserve the same drop takes 195 s (see the test above).
    """
    plant = await _plant(hass, dtu_network, freezer, reserve=10)
    await plant.run(200)
    assert sorted(v for _, _, v in plant.network.limits()) == [40, 40]
    plant.network.clear_limits()
    start = plant.sim.time
    plant.load = 230.0
    await plant.run(200)
    assert [v for _, _, v in plant.network.limits()] == [11, 11]
    assert plant.back_in_band(start) <= 75
    assert plant.back_in_band(start) * 2 < 195
    assert plant.worst_import(start) <= BAND


async def test_load_rise_after_curtailment_is_raised_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """From 11 % with a load of 500 W: one raising (24 %) per Inverter."""
    plant = await _plant(hass, dtu_network, freezer)
    plant.load = 230.0
    await plant.run(260)
    plant.network.clear_limits()
    start = plant.sim.time
    plant.load = 500.0
    await plant.run(30)
    for serial in (OMA, BUERO4):
        assert plant.commands(serial) == 1
    await plant.run(120)
    assert [v for _, _, v in plant.network.limits()] == [24, 24]
    assert plant.back_in_band(start) <= 60
    assert plant.worst_export(start) <= BAND


async def test_the_reserve_follows_the_sun(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The available power doubles: the output follows within 55 s.

    The ceiling moves up with the output (50, 55, 63, 70 %), so each Inverter
    gets four commands.
    """
    plant = await _plant(hass, dtu_network, freezer, reserve=10, load=2000.0)
    await plant.run(200)
    plant.network.clear_limits()
    start = plant.sim.time
    plant.dtu.inverters[0].available = 360.0
    plant.dtu.inverters[1].available = 900.0
    await plant.run(200)
    reached = min(t for t, _, power in plant.since(start) if power >= 1259)
    assert reached - start <= 55
    for serial in (OMA, BUERO4):
        assert plant.commands(serial) == 4
    assert sorted(v for _, _, v in plant.network.limits()) == [
        50,
        50,
        55,
        55,
        63,
        63,
        70,
        70,
    ]


async def test_a_steady_output_with_the_reserve_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    plant = await _plant(hass, dtu_network, freezer, reserve=10, load=2000.0)
    await plant.run(200)
    plant.network.clear_limits()
    await plant.run(600)  # forty runs
    assert plant.network.limits() == []


async def test_switching_curtailment_off_gives_everyone_100_despite_the_reserve(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    plant = await _plant(hass, dtu_network, freezer, reserve=10, load=2000.0)
    await plant.run(60)
    plant.network.clear_limits()
    await _curtail(hass, False)
    assert sorted(plant.network.limits()) == [(OMA, 1, 100), (BUERO4, 1, 100)]


async def test_the_reserve_applies_to_the_battery_backed_group_as_well(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    plant = await _plant(
        hass, dtu_network, freezer, reserve=10, load=2000.0, battery_backed=[BUERO4]
    )
    await plant.run(60)
    assert sorted(plant.network.limits()) == [(OMA, 1, 40), (BUERO4, 1, 40)]


async def test_battery_backed_group_load_drop_and_rise(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A Battery-backed Inverter alone delivers 600 W at 100 % (40 % of rated).

    Load drop to 200 W: lowered once to 13 %; back in the band after about
    190 s with 5 W of import at worst.
    Load rise to 450 W: one raising of the Battery-backed Inverter (30 %), the
    PV Inverter without sun is set to 100 % on the way; back in 50 s.
    """
    plant = await _plant(
        hass,
        dtu_network,
        freezer,
        battery_backed=[BUERO4],
        available=(0.0, 600.0),
        load=600.0,
    )
    await plant.run(30)
    plant.network.clear_limits()
    start = plant.sim.time
    plant.load = 200.0
    await plant.run(300)
    sent = [v for s, _, v in plant.network.limits() if s == BUERO4]
    assert sent == [13]
    assert plant.back_in_band(start) <= 200
    assert plant.worst_import(start) <= BAND

    plant.network.clear_limits()
    start = plant.sim.time
    plant.load = 450.0
    await plant.run(200)
    assert [v for s, _, v in plant.network.limits() if s == BUERO4] == [30]
    assert plant.commands(OMA) == 1
    assert plant.back_in_band(start) <= 60
    assert plant.worst_export(start) <= BAND


def _control(hass: HomeAssistant):
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return next(iter(entry.runtime_data.houses.values())).control


async def test_curtailing_means_holding_back_on_purpose(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The reserve's ceiling is no Curtailment; a lowering for export is."""
    plant = await _plant(hass, dtu_network, freezer, reserve=10)
    await plant.run(60)
    assert [v for _, _, v in plant.network.limits()] == [40, 40]
    assert not _control(hass).curtailing
    plant.load = 230.0
    await plant.run(30)
    assert _control(hass).curtailing


async def test_curtailing_without_the_reserve_follows_the_limits(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    plant = await _plant(hass, dtu_network, freezer)
    await plant.run(30)
    assert not _control(hass).curtailing
    plant.load = 230.0
    await plant.run(30)
    assert _control(hass).curtailing
    plant.load = 2000.0
    await plant.run(60)
    assert not _control(hass).curtailing
