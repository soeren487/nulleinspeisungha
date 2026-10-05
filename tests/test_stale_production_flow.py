"""The Battery-backed cap and release against stale production readings.

A PV Inverter and a Battery-backed Inverter that both produce, Inverters that
ramp as measured (``RampSimulator``) and production readings that are 10 to 45 s
old while Grid Power is fresh. Before the cap and the release waited for the
House to settle, the Battery-backed limit swung up and down in these runs: 47
commands in 1200 s with 20 s old readings and no AC Battery, 27 with one, and
targets alternating between 10 % and 95 %.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung import house_control
from tests.conftest import (
    DtuNetwork,
    RampSimulator,
    SimDtu,
    SimGx,
    SimHouse,
    setup_entry,
)
from tests.test_battery_curtailment_flow import POWER
from tests.test_curtailment_flow import (
    BUERO4,
    OMA,
    _curtail,
    _dtu,
    _grid,
    _set,
)

pytestmark = pytest.mark.measured_inverters

STEP = 5
INTERVAL = 15
LOAD_RISES, LOAD_FALLS, LOAD_RISES_AGAIN = 120, 420, 820
"""Times of the three load steps of the scenario."""

seen: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _record_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Note what the controller was given and answered in every run."""
    real = house_control.decide_house

    def spy(grid, setpoint, band, floor, pv, bb, consumption, headroom, curtailed):
        result = real(
            grid, setpoint, band, floor, pv, bb, consumption, headroom, curtailed
        )
        seen.update(
            consumption=consumption,
            pending=sum(i.pending_change for i in (*pv, *bb)),
            known=all(i.production is not None for i in bb),
        )
        return result

    seen.clear()
    monkeypatch.setattr(house_control, "decide_house", spy)


@dataclass
class Row:
    """What one step of 5 s looked like."""

    time: float
    load: float
    grid: float
    consumption: float | None
    """What the House computed in the run, ``None`` between two runs."""
    pending: float | None
    """Sum of the pending changes of all Inverters, as the controller got it."""
    known: bool | None
    """Whether every Battery-backed reading was newer than the last command."""
    pv_target: int | None
    bb_target: int | None
    pv_effective: float
    bb_effective: float
    pv_power: float
    bb_power: float
    sent: int
    """Limit commands sent to the Battery-backed Inverter in this step."""

    def __str__(self) -> str:
        consumption = "-" if self.consumption is None else f"{self.consumption:.0f}"
        return (
            f"{self.time:5.0f}s load={self.load:5.0f} grid={self.grid:6.1f} "
            f"consumption={consumption:>5} pending={self.pending} "
            f"target pv/bb={self.pv_target}/{self.bb_target} "
            f"effective={self.pv_effective:.0f}/{self.bb_effective:.0f} "
            f"production={self.pv_power:.0f}/{self.bb_power:.0f} sent={self.sent}"
        )


@dataclass
class Plant:
    """A House with a PV and a Battery-backed Inverter and a load, in steps of 5 s.

    Without an AC Battery Grid Power is the load minus the production. With one
    the battery holds Grid Power at its setpoint (0) and charges with the
    difference. The readings the DTU shows are ``age`` seconds old.
    """

    hass: HomeAssistant
    freezer: object
    network: DtuNetwork
    sim: RampSimulator
    dtu: SimDtu
    control: object
    gx: SimGx | None
    load: float
    age: float
    coordinators: list = field(default_factory=list)
    outputs: list[tuple[float, float, float]] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)

    async def run(self, seconds: float) -> None:
        pv_inverter, bb_inverter = self.dtu.inverters[:2]
        for _ in range(int(seconds / STEP)):
            self.freezer.tick(delta=timedelta(seconds=STEP))
            self.sim.advance(STEP)
            pv, bb = pv_inverter.power, bb_inverter.power
            self.outputs.append((self.sim.time, pv, bb))
            if self.gx is not None:
                self.gx.set(POWER, pv + bb - self.load)
                grid = 0.0
            else:
                grid = self.load - pv - bb
            _grid(self.hass, round(grid, 1))
            older = [o for o in self.outputs if o[0] <= self.sim.time - self.age]
            shown = older[-1] if older else self.outputs[0]
            pv_inverter.power, bb_inverter.power = shown[1], shown[2]
            pv_inverter.data_age = bb_inverter.data_age = int(self.age)
            self.network.apply()
            commands = len(self.network.limits())
            seen.clear()
            for coordinator in self.coordinators:
                await coordinator.async_refresh()
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            pv_inverter.power, bb_inverter.power = pv, bb  # the true output
            control = self.control
            self.rows.append(
                Row(
                    self.sim.time,
                    self.load,
                    grid,
                    seen.get("consumption"),
                    seen.get("pending"),
                    seen.get("known"),
                    control._sent.get(OMA),
                    control._sent.get(BUERO4),
                    self.sim.effective[OMA],
                    self.sim.effective[BUERO4],
                    pv,
                    bb,
                    sum(
                        1 for s, _, _ in self.network.limits()[commands:] if s == BUERO4
                    ),
                )
            )

    def trace(self) -> str:
        return "\n".join(str(row) for row in self.rows)

    def commands(self, start: float = 0, end: float = 1e9) -> int:
        """Limit commands the Battery-backed Inverter got between two times."""
        return sum(r.sent for r in self.rows if start < r.time <= end)

    def changes(self, start: float = 0, end: float = 1e9) -> list[int]:
        """The changes of the Battery-backed target between two times, in %."""
        found: list[int] = []
        last = 100  # an Inverter without a command is at 100 %
        for row in self.rows:
            if row.bb_target is None:
                continue
            if start < row.time <= end and row.bb_target != last:
                found.append(row.bb_target - last)
            last = row.bb_target
        return found

    def swing(self, start: float = 0, end: float = 1e9) -> int:
        """Highest minus lowest Battery-backed target between two times, in %."""
        targets = [
            100 if r.bb_target is None else r.bb_target
            for r in self.rows
            if start < r.time <= end
        ]
        return max(targets) - min(targets) if targets else 0

    def first_lowering(self, start: float) -> float | None:
        """When the Battery-backed target was lowered first after ``start``."""
        last = 100
        for row in self.rows:
            if row.bb_target is None:
                continue
            if row.time > start and row.bb_target < last:
                return row.time
            last = row.bb_target
        return None


async def make_plant(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    *,
    load: float = 700.0,
    age: float = 20.0,
    reserve: float = 0.0,
    ac_battery: bool = False,
    pv_available: float = 400.0,
    bb_available: float = 300.0,
    pv_reachable: bool = True,
) -> Plant:
    """OmaOpa (600 W, PV) and Büro 4 (1500 W, Battery-backed behind a DC Battery)."""
    dtu = _dtu()
    dtu.inverters[0].available = pv_available
    dtu.inverters[1].available = bb_available
    for inverter in dtu.inverters[:2]:
        inverter.power = inverter.available
        inverter.data_age = int(age)
    dtu.inverters[2].reachable = False
    dtu.inverters[0].reachable = pv_reachable
    gx = None
    if ac_battery:
        gx = SimGx()
        gx.charge(level=60.0, power=0.0)
    _grid(hass, 0.0 if gx else round(load - pv_available - bb_available, 1))
    entry = await setup_entry(
        hass,
        dtu_network,
        dtu,
        houses=[
            SimHouse("Home", inverters=[OMA, BUERO4], battery_backed=[BUERO4], gx=gx)
        ],
    )
    house = next(iter(entry.runtime_data.houses.values()))
    plant = Plant(
        hass,
        freezer,
        dtu_network,
        RampSimulator(dtu_network),
        dtu,
        house.control,
        gx,
        load,
        age,
        list(entry.runtime_data.dtus.values()),
    )
    if reserve:
        await _set(hass, "response_reserve", reserve)
    await _curtail(hass)
    return plant


async def _scenario(plant: Plant) -> None:
    """The load rises, later falls below what the group delivers, later rises."""
    await plant.run(LOAD_RISES)
    plant.load = 1000.0
    await plant.run(LOAD_FALLS - LOAD_RISES)
    plant.load = 150.0
    await plant.run(LOAD_RISES_AGAIN - LOAD_FALLS)
    plant.load = 1000.0
    await plant.run(400)


CASES = [
    pytest.param(False, 0.0, 20.0, id="no-battery-no-reserve"),
    pytest.param(False, 10.0, 20.0, id="no-battery-reserve-10"),
    pytest.param(True, 0.0, 10.0, id="battery-no-reserve-age-10"),
    pytest.param(True, 0.0, 20.0, id="battery-no-reserve"),
    pytest.param(True, 0.0, 45.0, id="battery-no-reserve-age-45"),
    pytest.param(True, 10.0, 20.0, id="battery-reserve-10"),
]


@pytest.mark.parametrize(("ac_battery", "reserve", "age"), CASES)
async def test_the_battery_backed_group_settles(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    ac_battery: bool,
    reserve: float,
    age: float,
) -> None:
    """One lowering when the load falls below the delivery, then only raising.

    Measured before the fix (reading age 20 s, no reserve): 47 commands and 22
    changes of direction without an AC Battery, 27 and 24 with one, the target
    swinging by 95 and 85 points.
    """
    plant = await make_plant(
        hass, dtu_network, freezer, ac_battery=ac_battery, reserve=reserve, age=age
    )
    await _scenario(plant)
    trace = plant.trace()
    # The load rise with the group delivering what it can: it is never lowered.
    assert all(c > 0 for c in plant.changes(LOAD_RISES, LOAD_FALLS)), trace
    # After the load fell, the group is lowered once and then only raised.
    changes = plant.changes(LOAD_FALLS - 1)
    assert changes[0] < 0, trace
    assert all(c > 0 for c in changes[1:]), trace
    # No command in the last 100 s: the group is at rest.
    assert plant.commands(plant.sim.time - 100) == 0, trace
    if ac_battery:
        # The AC Battery holds Grid Power, so the group has nothing to correct
        # but the cap and the release: a lowering and a raising.
        assert plant.commands() <= 3, trace
        assert plant.swing(plant.first_lowering(LOAD_FALLS)) <= 60, trace
        assert plant.commands(LOAD_RISES, LOAD_FALLS) == 0, trace
    else:
        assert plant.commands() <= 6, trace
        assert plant.commands(LOAD_RISES, LOAD_FALLS) <= 2, trace


async def test_a_normal_night_sends_nothing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """300 W from the DC Battery, PV off, the AC Battery covering a moving load."""
    plant = await make_plant(
        hass,
        dtu_network,
        freezer,
        ac_battery=True,
        pv_available=0.0,
        load=500.0,
        pv_reachable=False,
    )
    for load in (400, 900, 650, 400, 800, 900, 450, 700):
        plant.load = float(load)
        await plant.run(75)
    assert plant.network.limits() == [], plant.trace()


@pytest.mark.parametrize("age", [10.0, 45.0])
async def test_a_load_below_the_delivery_is_capped_within_three_intervals(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, age: float
) -> None:
    """At rest the group is lowered at the next runs; so it is the second time.

    The first time nothing is on its way. The second time the House is settled
    when the first lowering has shown in the readings, which is the effective
    limit reaching the target plus the reading age.
    """
    plant = await make_plant(
        hass,
        dtu_network,
        freezer,
        ac_battery=True,
        pv_available=0.0,
        load=500.0,
        age=age,
        pv_reachable=False,
    )
    await plant.run(60)
    plant.load = 200.0  # below the 300 W the DC Battery delivers
    await plant.run(3 * INTERVAL + STEP)
    first = plant.first_lowering(60)
    assert first is not None, plant.trace()
    assert first <= 60 + 3 * INTERVAL, plant.trace()
    assert plant.rows[-1].bb_target == 13  # 200 W of 1500 W

    # The output reaches the new limit; the load falls again at that moment,
    # while the readings still show the old output.
    while plant.rows[-1].bb_power > 200 + 1:
        await plant.run(STEP)
    landed = plant.sim.time
    plant.load = 100.0
    await plant.run(age + 3 * INTERVAL + STEP)
    second = next(
        (r.time for r in plant.rows if r.time > landed and r.bb_target == 7), None
    )
    assert second is not None, plant.trace()
    assert second <= landed + age + 3 * INTERVAL, plant.trace()


async def test_the_release_works_with_a_response_reserve(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The reserve's ceilings are no Curtailment: the DC Battery is released.

    Before, the PV group sat below 100 % only because of its ceiling, the release
    never fired and the Battery-backed limit stayed at 10 % under a 1000 W load.
    """
    plant = await make_plant(hass, dtu_network, freezer, ac_battery=True, reserve=10.0)
    await _scenario(plant)
    assert plant.rows[-1].pv_target < 100
    assert plant.rows[-1].bb_target > 10, plant.trace()
    assert plant.rows[-1].bb_power == pytest.approx(300, abs=1), plant.trace()


async def test_a_persistent_import_raises_a_limit_that_is_not_biting_at_once(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The group delivers 300 W under a limit of 405 W and the House imports.

    Before: 11 commands in steps of about 75 W. Now one command to 100 %.
    """
    plant = await make_plant(hass, dtu_network, freezer, age=20.0)
    await plant.run(LOAD_RISES)
    plant.load = 1000.0
    await plant.run(LOAD_FALLS - LOAD_RISES)
    plant.load = 150.0
    await plant.run(LOAD_RISES_AGAIN - LOAD_FALLS)
    assert plant.rows[-1].bb_target == 5
    start = plant.sim.time
    plant.load = 1000.0
    await plant.run(400)
    assert plant.changes(start) == [22, 73], plant.trace()
    assert plant.commands(start) == 2, plant.trace()
    assert plant.rows[-1].bb_target == 100


async def test_a_load_drop_after_the_jump_to_100_is_corrected_without_overshoot(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    plant = await make_plant(hass, dtu_network, freezer, age=20.0)
    await plant.run(LOAD_RISES)
    plant.load = 1000.0
    await plant.run(300)
    assert plant.rows[-1].bb_target == 100
    plant.load = 150.0
    start = plant.sim.time
    await plant.run(600)
    assert max(-r.grid for r in plant.rows if r.time > start) < 1000
    assert min(r.grid for r in plant.rows if r.time > start) > -600
    assert all(c < 0 for c in plant.changes(start)), plant.trace()
    assert abs(plant.rows[-1].grid) <= 30, plant.trace()
