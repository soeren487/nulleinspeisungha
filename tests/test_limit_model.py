"""The limit model: the effective limit, the pending change and the ceiling."""

from __future__ import annotations

import pytest

from custom_components.nulleinspeisung.limit_model import (
    LATENCY,
    LimitState,
    ceiling,
    is_limited,
    next_ceiling,
)

pytestmark = pytest.mark.measured_inverters

RATE = 0.5


def test_a_state_never_sent_to_is_at_100_and_settled() -> None:
    state = LimitState()
    assert state.effective_at(1000, RATE) == 100
    assert state.pending_change(1000, 600, 300, RATE) == 0


def test_the_limit_stays_for_the_latency() -> None:
    state = LimitState().retarget(100, 20, RATE)
    assert state.effective_at(100, RATE) == 100
    assert state.effective_at(100 + LATENCY, RATE) == 100


def test_lowering_slews_after_the_latency_and_stops_at_the_target() -> None:
    state = LimitState().retarget(0, 20, RATE)
    assert state.effective_at(LATENCY + 40, RATE) == pytest.approx(80)
    assert state.effective_at(LATENCY + 160, RATE) == pytest.approx(20)
    assert state.effective_at(LATENCY + 1000, RATE) == 20


def test_raising_slews_after_the_latency_and_stops_at_the_target() -> None:
    state = LimitState(10, 10, 0).retarget(0, 60, RATE)
    assert state.effective_at(LATENCY + 20, RATE) == pytest.approx(20)
    assert state.effective_at(LATENCY + 1000, RATE) == 60


def test_the_slew_rate_is_a_parameter() -> None:
    state = LimitState().retarget(0, 50, 2.0)
    assert state.effective_at(LATENCY + 10, 2.0) == pytest.approx(80)


def test_a_retarget_starts_from_the_effective_limit_at_that_time() -> None:
    state = LimitState().retarget(0, 20, RATE)
    now = LATENCY + 60  # effective 70
    again = state.retarget(now, 90, RATE)
    assert again.effective == pytest.approx(70)
    assert again.target == 90
    assert again.set_at == now
    assert again.effective_at(now + LATENCY + 10, RATE) == pytest.approx(75)


def test_a_retarget_within_the_latency_keeps_the_effective_limit() -> None:
    state = LimitState().retarget(0, 20, RATE).retarget(2, 50, RATE)
    assert state.effective == 100
    assert state.effective_at(2 + LATENCY + 20, RATE) == pytest.approx(90)


# -- the pending change ------------------------------------------------------

LOWERING = LimitState(100, 20, 0)
"""Lowering from 100 % to 20 % of 1000 W, sent at time 0."""


def test_lowering_with_production_above_the_final_allowance() -> None:
    """Producing 500 W, ending at 200 W: 300 W still to go, in the latency."""
    assert LOWERING.pending_change(1, 1000, 500, RATE) == pytest.approx(-300)


def test_lowering_with_production_below_the_final_allowance_changes_nothing() -> None:
    assert LOWERING.pending_change(1, 1000, 150, RATE) == 0
    assert LOWERING.pending_change(1, 1000, 0, RATE) == 0


def test_lowering_without_a_reading_assumes_the_whole_travel() -> None:
    # At time 1 the limit is still 100 %: 1000 W down to 200 W.
    assert LOWERING.pending_change(1, 1000, None, RATE) == pytest.approx(-800)
    # 40 s after the latency it is at 80 %: 800 W down to 200 W.
    assert LOWERING.pending_change(LATENCY + 40, 1000, None, RATE) == pytest.approx(
        -600
    )


def test_a_settled_lowering_has_nothing_pending() -> None:
    assert LOWERING.pending_change(LATENCY + 1000, 1000, 200, RATE) == 0
    assert LOWERING.pending_change(LATENCY + 1000, 1000, None, RATE) == 0


RAISING = LimitState(20, 70, 0)
"""Raising from 20 % to 70 % of 1000 W, sent at time 0."""


def test_raising_a_limited_inverter_will_deliver_the_rest() -> None:
    """Producing its 200 W limit: the output follows the limit up to 700 W."""
    assert RAISING.pending_change(1, 1000, 200, RATE) == pytest.approx(500)
    assert RAISING.pending_change(1, 1000, 185, RATE) == pytest.approx(515)


def test_raising_an_inverter_held_by_its_source_changes_nothing() -> None:
    """Producing 100 W below a 200 W limit: the source holds it, not the limit."""
    assert RAISING.pending_change(1, 1000, 100, RATE) == 0


def test_raising_without_a_reading_assumes_the_whole_travel() -> None:
    assert RAISING.pending_change(1, 1000, None, RATE) == pytest.approx(500)
    assert RAISING.pending_change(LATENCY + 40, 1000, None, RATE) == pytest.approx(300)


def test_a_raising_that_arrived_has_nothing_pending() -> None:
    assert RAISING.pending_change(LATENCY + 1000, 1000, 400, RATE) == 0


def test_a_reading_from_before_the_arrival_still_has_the_change_pending() -> None:
    """The effective limit reached 20 % at 165 s; a reading from 40 s before
    the time asked about still shows the output on its way down."""
    arrived = LATENCY + 160
    assert LOWERING.pending_change(arrived + 20, 1000, 300, RATE) == 0
    assert LOWERING.pending_change(arrived + 20, 1000, 300, RATE, reading_age=40) == (
        pytest.approx(-100)
    )
    assert LOWERING.pending_change(arrived + 20, 1000, 200, RATE, reading_age=40) == 0


def test_a_reading_from_before_the_arrival_of_a_raising_is_not_final() -> None:
    arrived = LATENCY + 100
    assert RAISING.pending_change(arrived + 20, 1000, 650, RATE) == 0
    assert RAISING.pending_change(arrived + 20, 1000, 650, RATE, reading_age=40) == (
        pytest.approx(50)
    )


# -- the ceiling -------------------------------------------------------------


def test_the_ceiling_is_the_output_plus_the_reserve() -> None:
    assert ceiling(300, 1000, 10) == pytest.approx(40)


def test_the_ceiling_is_at_most_100() -> None:
    assert ceiling(950, 1000, 10) == 100


def test_the_ceiling_without_a_reading_is_100() -> None:
    assert ceiling(None, 1000, 10) == 100


def test_the_first_ceiling_is_the_fresh_one() -> None:
    assert next_ceiling(None, 40, 10) == 40


def test_a_small_change_keeps_the_applied_ceiling() -> None:
    assert next_ceiling(40, 44, 10) == 40
    assert next_ceiling(40, 35, 10) == 40  # exactly half the reserve


def test_a_larger_change_moves_the_ceiling() -> None:
    assert next_ceiling(40, 45.5, 10) == 45.5
    assert next_ceiling(40, 30, 10) == 30


def test_the_ceiling_moves_to_100_and_back_when_the_reading_goes() -> None:
    assert next_ceiling(40, ceiling(None, 1000, 10), 10) == 100
    assert next_ceiling(100, 40, 10) == 40


def test_a_reading_older_than_the_ramp_still_counts_as_limited() -> None:
    """Measured 10 s ago at the limit of that time (200 W): limited, not held by
    the source, though the effective limit has moved on since."""
    state = LimitState(20, 70, 0)
    now = LATENCY + 20  # effective 30 %: 300 W
    assert state.pending_change(now, 1000, 200, RATE) == 0
    assert state.pending_change(now, 1000, 200, RATE, reading_age=20) == (
        pytest.approx(500)
    )


def test_limited_right_now_follows_the_reading() -> None:
    state = LimitState(40, 40, 0)  # 400 W of 1000 W
    assert is_limited(state, 100, 1000, 399, RATE) is True
    assert is_limited(state, 100, 1000, 385, RATE) is True  # within the margin
    assert is_limited(state, 100, 1000, 300, RATE) is False
    assert is_limited(state, 100, 1000, None, RATE) is None
