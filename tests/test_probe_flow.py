"""Curtailment that limits only export, end to end (ticket 29).

The plant has ramping PV Inverters (600 W and 1500 W rated), a load, and an AC
Battery that regulates Grid Power to its setpoint like the real one: it ramps
at 400 W/s, is limited by its charge power, and its meter wanders by tens to
hundreds of watts. The House acts on the mean of the last minute of that meter.
"""

from __future__ import annotations

import random

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nulleinspeisung import house_control
from tests.conftest import DtuNetwork, SimAcBattery, SimGx
from tests.test_ramp_flow import (
    BAND,
    BUERO4,
    OMA,
    STEP,
    Plant,
    _control,
    _plant,
    _set,
)

pytestmark = pytest.mark.measured_inverters

SUN = (600.0, 1500.0)
"""What the two Inverters can deliver at most: 2100 W."""
CHARGE = 1900.0
"""The charge power of the AC Battery in the tests, in W."""


async def _site(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    *,
    load: float = 300.0,
    available: tuple[float, float] = SUN,
    reserve: float = 0.0,
    level: float = 40.0,
    noise: float = 100.0,
    seed: int = 1,
    current_limit: float = 100.0,
    max_charge: float = CHARGE,
    battery: bool = True,
) -> Plant:
    """A House with an AC Battery (or without), Curtailment switched on."""
    sim_battery = None
    if battery:
        sim_battery = SimAcBattery(
            SimGx(),
            level=level,
            noise=noise,
            seed=seed,
            current_limit=current_limit,
            max_charge=max_charge,
        )
    plant = await _plant(
        hass,
        dtu_network,
        freezer,
        load=load,
        available=available,
        reserve=reserve,
        battery=sim_battery,
    )
    if battery:
        # The setting says what the battery can take; keep it true to the battery.
        await _set(hass, "maximum_charge_power", max_charge)
    return plant


def _percent(plant: Plant, serial: str) -> float:
    """The last limit sent to an Inverter, 100 before any."""
    sent = [p for _, s, p in plant.sent if s == serial]
    return sent[-1] if sent else 100.0


def _lowerings(plant: Plant, since: float = 0.0) -> int:
    """How many commands lowered an Inverter's limit after ``since``."""
    last: dict[str, float] = {}
    count = 0
    for time, serial, percent in plant.sent:
        if time > since and percent < last.get(serial, 100.0):
            count += 1
        last[serial] = percent
    return count


def _exported_wh(plant: Plant, since: float = 0.0) -> float:
    """Energy that went to the grid after ``since``, in Wh (noise excluded)."""
    return sum(-g for t, g, _ in plant.since(since) if g < 0) * STEP / 3600


async def _limited_by_a_slow_charger(plant: Plant, seconds: float = 240) -> None:
    """The battery can take only ~1 kW at first, so the Inverters are lowered."""
    plant.battery.current_limit = 19.0
    await plant.run(seconds)


# -- the reported situation ----------------------------------------------------


@pytest.mark.parametrize("seed", [1, 5])
async def test_a_battery_that_can_take_more_ends_charging_at_what_it_can_take(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, seed: int
) -> None:
    """The reported situation: Inverters held low while the battery has room.

    The sun gives 2100 W, the load is 300 W. At first the battery's BMS allows
    only about 1 kW, so the Inverters are lowered for the export (legitimately).
    Then the BMS allows 2 kW: the battery could take 1800 W, the Inverters are
    still limited. The meter wanders by 100 W (one standard deviation).
    """
    plant = await _site(hass, dtu_network, freezer, seed=seed)
    await _limited_by_a_slow_charger(plant)
    assert plant.battery.power < 1100
    assert min(p for _, s, p in plant.sent if s == BUERO4) < 70  # lowered for export

    plant.battery.current_limit = 40.0
    await plant.run(360)
    # What the battery can take: the sun's 2100 W minus the load.
    assert plant.battery.power >= 1750 or plant.production() >= 2090
    assert _percent(plant, OMA) >= 95
    assert _percent(plant, BUERO4) >= 95


async def test_the_probe_finds_room_the_battery_does_not_announce(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The Maximum Charge Power is set too low (1 kW): the headroom stays zero.

    Without an estimate to rely on, the Inverters are raised step by step until
    the battery takes what it can (1.9 kW) and the sun is used.
    """
    plant = await _site(hass, dtu_network, freezer)
    await _set(hass, "maximum_charge_power", 1000)
    await plant.run(240)
    assert plant.battery.power >= 1750 or plant.production() >= 2090
    assert _control(hass)._house.battery_headroom() == 0


# -- a load comes on while the Inverters are limited -----------------------------


def _first_command_after(plant: Plant, start: float) -> float | None:
    times = [t for t, _, _ in plant.sent if t > start]
    return min(times) - start if times else None


async def test_a_load_switched_on_with_a_full_battery_raises_the_limits(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The battery covers the load, so the meter never shows an import.

    Full battery, 2100 W of sun, a load of 300 W: the Inverters sit at the load.
    A load of 700 W comes on; the battery discharges to cover it. The limits
    start to rise within three Update Intervals and reach the new load.
    """
    plant = await _site(hass, dtu_network, freezer, level=100.0, noise=0.0)
    await plant.run(300)
    assert plant.production() < 450
    start = plant.sim.time
    plant.load = 700.0
    await plant.run(5)
    assert plant.battery.power < -300  # the battery covers: no import shows
    reached = None
    for _ in range(120):
        await plant.run(5)
        assert plant.worst_import(start) <= BAND, "an import showed at the meter"
        if plant.production() >= 690:
            reached = plant.sim.time - start
            break
    first = _first_command_after(plant, start)
    print(f"load on, full battery: first command after {first} s, reached in {reached}")
    assert first is not None
    assert first <= 45
    assert reached is not None
    assert reached <= 80  # about four Update Intervals after the first command


async def test_a_load_switched_on_without_a_battery_raises_the_limits(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The import shows at the meter: the limits rise at the next run."""
    plant = await _site(hass, dtu_network, freezer, battery=False, load=300.0)
    await plant.run(300)
    assert plant.production() < 450
    start = plant.sim.time
    plant.load = 700.0
    reached = None
    for _ in range(40):
        await plant.run(5)
        if plant.production() >= 690:
            reached = plant.sim.time - start
            break
    first = _first_command_after(plant, start)
    print(f"load on, no battery: first command after {first} s, reached in {reached}")
    assert first is not None
    assert first <= 15
    assert reached is not None
    assert reached <= 90


async def test_a_load_switched_on_while_the_battery_has_room_is_covered_too(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The battery charges at 1.8 kW of 1.9 kW; a load of 700 W comes on.

    The Inverters were lowered while the battery was full; it then drained a
    little and has room again. The load is covered by the Inverters, not the
    battery, within a few Update Intervals.
    """
    plant = await _site(hass, dtu_network, freezer, level=100.0, noise=0.0)
    await plant.run(300)
    plant.battery.level = 60.0
    start = plant.sim.time
    plant.load = 700.0
    await plant.run(5)
    reached = None
    for _ in range(120):
        await plant.run(5)
        if plant.battery.power >= -50 and plant.production() >= 690:
            reached = plant.sim.time - start
            break
    print(f"load on, battery with room: covered after {reached} s")
    assert reached is not None
    assert reached <= 80


async def test_a_discharge_does_not_count_during_a_block_or_grid_charging(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """A battery held on purpose, or charged from the grid, says nothing of loads."""
    plant = await _site(hass, dtu_network, freezer, level=100.0, noise=0.0)
    await plant.run(300)
    house = _control(hass)._house
    for wanted in ("max_discharge_power", "setpoint"):
        setattr(house.wanted_overrides, wanted, 0.0)
        plant.sent.clear()
        plant.load = 700.0
        # The battery covers the load (the block is not honoured by this
        # simulator's GX state); the House must not raise for it.
        plant.battery.max_discharge = 2500.0
        await plant.run(60)
        assert plant.production() < 450, "raised for a discharge"
        setattr(house.wanted_overrides, wanted, None)
        plant.load = 300.0
        await plant.run(300)


# -- export that persists, and only that --------------------------------------


async def test_a_spike_of_export_lowers_nothing_and_a_lasting_export_lowers(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """The battery charges at 1.8 of 1.9 kW; the load drops and the battery
    cannot take the 200 W more: that is export, for 5 s and then for good."""
    plant = await _site(hass, dtu_network, freezer, noise=0.0)
    await plant.run(240)  # the battery has ramped up
    assert plant.battery.power > 1700
    plant.sent.clear()
    plant.load = 0.0
    await plant.run(5)
    assert plant.worst_export(plant.sim.time - 10) >= 150
    plant.load = 300.0
    await plant.run(180)
    assert plant.sent == [], "a spike of one run lowered the limits"

    plant.load = 0.0
    await plant.run(30)
    assert plant.sent == [], "export lowers only when it persists in two runs"
    await plant.run(150)
    assert plant.sent, "export that persists was not lowered"
    # (At most one probe above what the battery takes: 3 % of 2100 W.)
    assert plant.production() <= CHARGE + 70
    assert plant.worst_export(plant.sim.time - 30) <= 70


# -- a battery that cannot take more ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "noise"),
    [("full", 100.0), ("at its charge power", 100.0), ("full", 0.0)],
    ids=["full-noisy", "at-charge-power-noisy", "full-calm"],
)
async def test_a_battery_that_cannot_take_more_settles(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, kind: str, noise: float
) -> None:
    """More sun than load and battery can use: probes become rarer, nothing swings.

    Measured over 30 minutes (the numbers are printed; the bounds are those
    measured, with a little room): commands per Inverter, the largest real
    export and the energy exported while probing. With a noisy meter the mean
    sometimes shows an import beyond the band, which ends the back-off, so it
    does not climb as far as with a calm one.
    """
    if kind == "full":
        plant = await _site(hass, dtu_network, freezer, level=100.0, noise=noise)
    else:
        # The BMS allows 19 A (about 1 kW): the battery charges at that power.
        plant = await _site(hass, dtu_network, freezer, current_limit=19.0, noise=noise)
    control = _control(hass)
    await plant.run(300)  # the first lowering and its convergence
    start = plant.sim.time
    commands = len(plant.sent)
    probing = [0, 0, 0]
    for run in range(120):  # 30 minutes
        await plant.run(15)
        if control.state.value == "probing":
            probing[run // 40] += 1
    per_inverter = (len(plant.sent) - commands) / 2
    largest_export = plant.worst_export(start)
    energy = _exported_wh(plant, start)
    print(
        f"settle[{kind}, noise {noise}]: commands/Inverter {per_inverter}, largest "
        f"export {largest_export:.0f} W, exported {energy:.1f} Wh, probing runs per "
        f"10 min {probing}, back-off {control.probe_backoff}"
    )
    assert per_inverter <= 35
    assert largest_export <= 250
    assert energy <= 15
    assert control.probe_backoff == house_control.MAXIMUM_BACKOFF
    assert sum(probing) <= 8  # rare probes
    assert probing[2] <= probing[0]


# -- noise ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_meter_noise_does_not_hold_the_limits_down_while_the_battery_has_room(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer, seed: int
) -> None:
    """The meter wanders by 100 W (one standard deviation) for half an hour.

    The band is 30 W and single readings are far beyond it all the time; the
    House acts on the mean of a minute. Its mean still strays beyond the band
    now and then (a standard deviation of 16 to 22 W), twice in a row about once
    in ten minutes: such a lowering is small (a few points) and the next raise
    takes it back. The real system saw 114 changes in an hour before.
    """
    plant = await _site(hass, dtu_network, freezer, seed=seed)
    await _limited_by_a_slow_charger(plant)
    plant.battery.current_limit = 40.0
    start = plant.sim.time
    commands = len(plant.sent)
    await plant.run(1800)
    per_inverter = (len(plant.sent) - commands) / 2
    lowest = min(p for t, s, p in plant.sent if s == BUERO4 and t > start + 120)
    average = sum(p for _, _, p in plant.since(start + 120)) / len(
        plant.since(start + 120)
    )
    print(
        f"noise 100 W, seed {seed}: {per_inverter} commands per Inverter, "
        f"{_lowerings(plant, start)} lowerings, lowest {lowest} %, "
        f"average production {average:.0f} W"
    )
    assert per_inverter <= 9
    assert lowest >= 90
    assert average >= 2040  # of 2100 W
    assert _lowerings(plant, start) <= 8
    assert plant.battery.power >= 1650


async def test_a_stormy_meter_with_big_loads_switching_recovers_to_100_percent(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Noise of 375 W and loads of up to 3 kW switching for 20 minutes.

    The battery follows at 400 W/s, so every load step leaks through the meter
    as a spike of kilowatts. The House must not be lowered below what the mean
    export justifies, and must be back at 100 % a few minutes after the storm.
    """
    plant = await _site(hass, dtu_network, freezer, noise=375.0, seed=7)
    await _limited_by_a_slow_charger(plant)
    plant.battery.current_limit = 40.0
    start = plant.sim.time
    random.seed(11)
    storm = random.Random(11)
    while plant.sim.time < start + 1200:
        plant.load = storm.choice([300.0, 300.0, 1200.0, 2500.0, 3000.0])
        await plant.run(storm.choice([10, 15, 20, 30, 45]))
    plant.load = 300.0
    during = min(
        min(p for t, s, p in plant.sent if s == serial and t > start)
        for serial in (OMA, BUERO4)
    )
    print(
        f"storm: lowest limit {during} %, commands {len(plant.sent)}, "
        f"largest export {plant.worst_export(start):.0f} W"
    )
    # Never lowered by more than the mean export of a few runs justifies.
    assert during >= 60
    after = plant.sim.time
    await plant.run(300)
    back = [p for t, s, p in plant.sent if s == BUERO4 and t > after]
    assert 100 in back, "not back at 100 % within five minutes"
    await plant.run(300)
    assert min(p for t, s, p in plant.sent if s == BUERO4 and t > after) >= 85


# -- a House without an AC Battery ---------------------------------------------


async def test_a_house_without_a_battery_keeps_probing_with_back_off_and_does_not_swing(
    hass: HomeAssistant, dtu_network: DtuNetwork, freezer
) -> None:
    """Fully curtailed for export (sun 2100 W, load 300 W, nothing to charge).

    Each probe adds 63 W of export that is taken back; the waits between the
    probes grow, so the House settles into rare, small excursions.
    """
    plant = await _site(hass, dtu_network, freezer, battery=False)
    await plant.run(300)
    start = plant.sim.time
    commands = len(plant.sent)
    probing = [0, 0, 0]
    control = _control(hass)
    for run in range(120):
        await plant.run(15)
        if control.state.value == "probing":
            probing[run // 40] += 1
    per_inverter = (len(plant.sent) - commands) / 2
    print(
        f"no battery: commands/Inverter {per_inverter}, largest export "
        f"{plant.worst_export(start):.0f} W, exported {_exported_wh(plant, start):.1f}"
        f" Wh, probing runs per 10 min {probing}, back-off {control.probe_backoff}"
    )
    assert probing[0] > 0
    assert probing[2] <= probing[0]
    assert control.probe_backoff == 600
    assert plant.worst_export(start) <= 100
    assert _exported_wh(plant, start) <= 10
    assert per_inverter <= 20


# -- slow Inverters -------------------------------------------------------------


async def test_slow_inverters_are_not_probed_while_a_raise_is_on_its_way(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    freezer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inverters at 0.2 %/s (the House knows): no probe on top of a raise on its way."""
    seen: list[tuple[str, float]] = []
    real = house_control.decide_house

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        pending = sum(i.pending_change for i in args[4])
        seen.append((result.state.value, pending))
        return result

    monkeypatch.setattr(house_control, "decide_house", spy)
    plant = await _site(hass, dtu_network, freezer, reserve=10.0)
    plant.sim.rate = 0.2
    await _set(hass, "limit_slew_rate", 0.2)
    await _limited_by_a_slow_charger(plant)
    plant.battery.current_limit = 40.0
    seen.clear()
    await plant.run(900)
    # Never a probe while the PV group still has more than the band to rise.
    assert all(pending <= BAND for state, pending in seen if state == "probing")
    # With the response reserve at 10 % the House still reaches what the battery
    # can take.
    assert plant.battery.power >= 1750 or plant.production() >= 2090
