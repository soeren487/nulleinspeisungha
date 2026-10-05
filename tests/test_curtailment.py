"""The Curtailment controller and the limit split, tested directly."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.nulleinspeisung.curtailment import (
    ControllableInverter,
    ControlState,
    decide_house,
)
from custom_components.nulleinspeisung.limit_split import split_equally


def _inv(
    rated: float = 1000, production: float | None = 1000, limit: float = 100
) -> ControllableInverter:
    return ControllableInverter(rated_power=rated, production=production, limit=limit)


@dataclass(frozen=True)
class _Single:
    """The answer for a House with only a PV group."""

    state: ControlState
    allowed_power: float | None


def _decide(grid: float, *inverters: ControllableInverter, **kwargs: float):
    settings = {
        "setpoint": 0.0,
        "band": 30.0,
        "floor": 5.0,
        "battery_headroom": 0.0,
    } | kwargs
    decision = decide_house(
        grid,
        settings["setpoint"],
        settings["band"],
        settings["floor"],
        inverters,
        [],
        None,
        settings["battery_headroom"],
    )
    return _Single(decision.state, decision.pv.allowed_power if decision.pv else None)


@pytest.mark.parametrize("grid", [-30.0, -10.0, 0.0, 15.0, 30.0])
def test_inside_the_band_nothing_changes(grid: float) -> None:
    """Within the tolerance band the allowance stays as it is."""
    decision = _decide(grid, _inv(production=500, limit=60))
    assert decision.state is ControlState.HOLDING
    assert decision.allowed_power == pytest.approx(600)


def test_export_above_setpoint_lowers_by_the_excess() -> None:
    """Exporting 200 W with 1000 W produced allows 800 W."""
    decision = _decide(-200.0, _inv(production=1000, limit=100))
    assert decision.state is ControlState.LOWERING
    assert decision.allowed_power == pytest.approx(800)


def test_import_raises_by_the_deficit() -> None:
    """Importing 150 W with 400 W allowed and produced allows 550 W."""
    decision = _decide(150.0, _inv(production=400, limit=40))
    assert decision.state is ControlState.RAISING
    assert decision.allowed_power == pytest.approx(550)


@pytest.mark.parametrize(
    ("setpoint", "grid", "state", "allowed"),
    [
        (100.0, -100.0, ControlState.HOLDING, 500),
        (100.0, -300.0, ControlState.LOWERING, 300),
        (100.0, 0.0, ControlState.RAISING, 600),
        (-100.0, 100.0, ControlState.HOLDING, 500),
        (-100.0, 0.0, ControlState.LOWERING, 400),
        (-100.0, 300.0, ControlState.RAISING, 700),
    ],
)
def test_signed_setpoints(
    setpoint: float, grid: float, state: ControlState, allowed: float
) -> None:
    """Export setpoints are positive; the Grid Power aimed for is the negation."""
    decision = _decide(grid, _inv(production=500, limit=50), setpoint=setpoint)
    assert decision.state is state
    assert decision.allowed_power == pytest.approx(allowed)


def test_band_edge_just_outside_acts() -> None:
    """A deviation of 31 W against a 30 W band is acted on."""
    decision = _decide(-31.0, _inv(production=1000, limit=100))
    assert decision.state is ControlState.LOWERING
    assert decision.allowed_power == pytest.approx(969)


def test_cloudy_day_lowering_starts_from_actual_production() -> None:
    """Allowed 1000 W but only 300 W produced: exporting 100 W allows 200 W."""
    decision = _decide(-100.0, _inv(production=300, limit=100))
    assert decision.allowed_power == pytest.approx(200)


def test_lowering_starts_from_allowance_when_that_is_lower() -> None:
    """Allowed 400 W while 450 W were still produced: start from 400 W."""
    decision = _decide(-100.0, _inv(production=450, limit=40))
    assert decision.allowed_power == pytest.approx(300)


def test_cloudy_day_raising_starts_from_allowance() -> None:
    """Allowed 800 W, 300 W produced: importing 100 W allows 900 W."""
    decision = _decide(100.0, _inv(production=300, limit=80))
    assert decision.allowed_power == pytest.approx(900)


def test_raising_starts_from_allowance_even_when_production_reads_higher() -> None:
    """Allowed 400 W, a stale reading of 450 W: importing 100 W allows 500 W."""
    decision = _decide(100.0, _inv(production=450, limit=40))
    assert decision.allowed_power == pytest.approx(500)


def test_lowering_with_production_above_allowance_uses_allowance() -> None:
    """A reading that has not caught up with the limit does not count."""
    decision = _decide(-100.0, _inv(production=900, limit=40))
    assert decision.allowed_power == pytest.approx(300)


def test_clamped_at_the_floor() -> None:
    """The allowance never goes below the floor share of the rated power."""
    decision = _decide(-5000.0, _inv(rated=1000), floor=10.0)
    assert decision.allowed_power == pytest.approx(100)


def test_clamped_at_full_rated_power() -> None:
    """The allowance never goes above 100 % of the rated power."""
    decision = _decide(5000.0, _inv(rated=1000, production=100, limit=50))
    assert decision.allowed_power == pytest.approx(1000)


def test_unknown_production_falls_back_to_allowance() -> None:
    """Without any known production the allowance is the base."""
    lowering = _decide(-100.0, _inv(production=None, limit=50))
    assert lowering.allowed_power == pytest.approx(400)
    raising = _decide(100.0, _inv(production=None, limit=50))
    assert raising.allowed_power == pytest.approx(600)


def test_lowering_with_mixed_known_and_unknown_production() -> None:
    """An Inverter with unknown production counts with its own allowance."""
    decision = _decide(
        -100.0, _inv(production=200, limit=100), _inv(production=None, limit=50)
    )
    # min(1000, 200) + min(500, 500) = 700 W, less 100 W
    assert decision.allowed_power == pytest.approx(600)


def test_lowering_takes_the_smaller_per_inverter() -> None:
    """One cloudy and one saturated Inverter are each limited by their smaller."""
    decision = _decide(
        -100.0, _inv(production=200, limit=100), _inv(production=900, limit=50)
    )
    # min(1000, 200) + min(500, 900) = 700 W, less 100 W
    assert decision.allowed_power == pytest.approx(600)


def test_group_is_handled_as_one() -> None:
    """Two Inverters share one allowance."""
    decision = _decide(
        -200.0,
        _inv(rated=600, production=300, limit=100),
        _inv(rated=1500, production=700, limit=100),
    )
    assert decision.allowed_power == pytest.approx(800)


def test_no_controllable_inverter() -> None:
    """With nothing to control the controller says so."""
    decision = _decide(-500.0)
    assert decision.state is ControlState.NO_INVERTER
    assert decision.allowed_power is None


# -- limit split ---------------------------------------------------------


def test_split_gives_everyone_the_same_percent() -> None:
    """1050 W of 2100 W rated is 50 % for every Inverter."""
    assert split_equally(1050, {"a": 600, "b": 1500}, 5) == {"a": 50, "b": 50}


@pytest.mark.parametrize(
    ("allowed", "expected"),
    [(504, 24), (494, 24), (483, 23), (1050, 50), (1055, 50), (1060, 50)],
)
def test_split_rounds_to_a_whole_percent(allowed: float, expected: int) -> None:
    """The percent is rounded to the nearest whole number."""
    assert split_equally(allowed, {"a": 2100}, 5) == {"a": expected}


def test_split_rounds_half_up() -> None:
    """0.5 rounds up, not to the even neighbour."""
    assert split_equally(101, {"a": 100, "b": 100}, 5) == {"a": 51, "b": 51}
    assert split_equally(100, {"a": 200}, 5) == {"a": 50}
    assert split_equally(99, {"a": 200}, 5) == {"a": 50}  # 49.5


def test_split_never_goes_below_the_floor() -> None:
    """A tiny allowance is lifted to the floor."""
    assert split_equally(0, {"a": 1000}, 5) == {"a": 5}
    assert split_equally(10, {"a": 1000}, 7.5) == {"a": 8}


def test_split_never_exceeds_100() -> None:
    """More than the rated power is capped at 100 %."""
    assert split_equally(5000, {"a": 1000, "b": 1000}, 5) == {"a": 100, "b": 100}


def test_split_of_nothing() -> None:
    """No Inverters, no limits."""
    assert split_equally(100, {}, 5) == {}


def test_headroom_raises_the_allowance_inside_the_band() -> None:
    """At the target, a battery that could take 500 W lets Inverters produce more."""
    decision = _decide(0.0, _inv(production=400, limit=40), battery_headroom=500.0)
    assert decision.state is ControlState.RAISING
    assert decision.allowed_power == pytest.approx(900)


def test_headroom_is_added_to_a_deficit() -> None:
    """Importing 100 W and a headroom of 300 W raises by 400 W."""
    decision = _decide(100.0, _inv(production=400, limit=40), battery_headroom=300.0)
    assert decision.allowed_power == pytest.approx(800)


def test_headroom_within_the_band_holds() -> None:
    """A headroom smaller than the band is not worth a change."""
    decision = _decide(0.0, _inv(production=400, limit=40), battery_headroom=20.0)
    assert decision.state is ControlState.HOLDING
    assert decision.allowed_power == pytest.approx(400)


def test_headroom_is_capped_by_the_rated_power() -> None:
    """The allowance never exceeds what the Inverters are rated for."""
    decision = _decide(0.0, _inv(production=900, limit=90), battery_headroom=5000.0)
    assert decision.allowed_power == pytest.approx(1000)


def test_headroom_is_ignored_when_exporting_beyond_the_band() -> None:
    """The battery evidently does not absorb the surplus: lower as without it."""
    decision = _decide(-200.0, _inv(production=1000), battery_headroom=800.0)
    assert decision.state is ControlState.LOWERING
    assert decision.allowed_power == pytest.approx(800)


def test_headroom_counts_at_the_edge_of_the_band() -> None:
    """Export exactly at the edge of the band is not beyond it."""
    decision = _decide(-30.0, _inv(production=500, limit=50), battery_headroom=200.0)
    assert decision.state is ControlState.RAISING
    assert decision.allowed_power == pytest.approx(670)


def test_no_headroom_behaves_as_before() -> None:
    """The default headroom of zero changes nothing."""
    for grid in (-200.0, -10.0, 0.0, 150.0):
        inverter = _inv(production=400, limit=40)
        assert _decide(grid, inverter) == _decide(grid, inverter, battery_headroom=0.0)


# -- pending changes -----------------------------------------------------------


def _pending(
    pending: float, production: float | None = 1000, limit: float = 100
) -> ControllableInverter:
    return ControllableInverter(
        rated_power=1000, production=production, limit=limit, pending_change=pending
    )


def _full(grid: float, pv, bb=(), headroom: float = 0.0, consumption=None):
    return decide_house(grid, 0.0, 30.0, 5.0, pv, list(bb), consumption, headroom)


def test_a_pending_lowering_cancels_an_equal_export() -> None:
    decision = _full(-400, [_pending(-400)])
    assert decision.state is ControlState.HOLDING
    assert decision.pv.allowed_power == pytest.approx(1000)
    assert decision.corrected_deviation == pytest.approx(0)


def test_a_pending_raising_cancels_an_equal_import() -> None:
    decision = _full(400, [_pending(400, production=300, limit=30)])
    assert decision.state is ControlState.HOLDING
    assert not decision.pv.changed
    assert decision.pv.allowed_power == pytest.approx(300)


def test_a_partial_pending_change_leaves_the_rest() -> None:
    decision = _full(-400, [_pending(-250)])
    assert decision.state is ControlState.LOWERING
    assert decision.corrected_deviation == pytest.approx(-150)
    assert decision.pv.allowed_power == pytest.approx(850)
    raising = _full(400, [_pending(250, production=300, limit=30)])
    assert raising.state is ControlState.RAISING
    assert raising.pv.allowed_power == pytest.approx(450)


def test_pending_changes_of_both_groups_are_summed() -> None:
    decision = _full(-400, [_pending(-150)], [_pending(-250)])
    assert decision.state is ControlState.HOLDING
    assert decision.corrected_deviation == pytest.approx(0)


def test_a_pending_change_never_reverses_the_deviation() -> None:
    """Readings older than the Grid Power must not turn an export into a raise."""
    exporting = _full(-100, [_pending(-500)])
    assert exporting.state is ControlState.HOLDING
    assert exporting.corrected_deviation == 0
    importing = _full(100, [_pending(500, production=300, limit=30)])
    assert importing.state is ControlState.HOLDING
    assert importing.corrected_deviation == 0


def test_the_band_test_uses_the_corrected_deviation() -> None:
    """An export of 100 W is beyond the band, but not with 90 W on its way."""
    assert _full(-100, [_pending(0)]).state is ControlState.LOWERING
    assert _full(-100, [_pending(-90)]).state is ControlState.HOLDING


def test_headroom_counts_unless_the_corrected_deviation_exports_beyond_the_band() -> (
    None
):
    """400 W export with the same lowering on its way is no export: headroom counts."""
    waiting = _full(-400, [_pending(-400)], headroom=500)
    assert waiting.state is ControlState.RAISING
    assert waiting.pv.allowed_power == pytest.approx(1000)  # at most the rated power
    assert waiting.corrected_deviation == pytest.approx(0)
    # Without the lowering on its way the export is beyond the band: no headroom.
    exporting = _full(-400, [_pending(0)], headroom=500)
    assert exporting.state is ControlState.LOWERING
    assert exporting.corrected_deviation == pytest.approx(-400)


def test_the_corrected_deviation_without_pending_is_the_deviation() -> None:
    assert _full(-400, [_pending(0)]).corrected_deviation == pytest.approx(-400)
    assert _full(120, [_pending(0)]).corrected_deviation == pytest.approx(120)
    assert decide_house(
        -50, 0.0, 30.0, 5.0, [], [], None
    ).corrected_deviation == pytest.approx(-50)


@pytest.mark.parametrize("grid", [-800.0, -100.0, -20.0, 0.0, 40.0, 700.0])
def test_without_pending_changes_the_result_is_the_same_as_ever(grid: float) -> None:
    plain = _decide(grid, _inv(production=600, limit=70))
    explicit = _full(grid, [_pending(0, production=600, limit=70)])
    assert explicit.state is plain.state
    assert explicit.pv.allowed_power == pytest.approx(plain.allowed_power)
