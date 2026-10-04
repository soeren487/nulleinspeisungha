"""Direct tests of the stuck decision and the restart policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from custom_components.nulleinspeisung.dtu_health import (
    Action,
    InverterObservation,
    RestartPolicy,
    StuckSettings,
    is_stuck,
)

SETTINGS = StuckSettings(sun_angle=5, staleness_time=120)
T0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
WAIT = timedelta(minutes=3)


def stale() -> InverterObservation:
    """An Inverter that has not delivered data for a long time."""
    return InverterObservation(data_age=5000)


def fresh() -> InverterObservation:
    """An Inverter that delivered data a moment ago."""
    return InverterObservation(data_age=3)


def test_stuck_when_sun_high_and_all_inverters_stale() -> None:
    """By day with only stale data the DTU is stuck."""
    assert is_stuck([stale(), stale()], 30, SETTINGS)


def test_not_stuck_when_sun_below_angle() -> None:
    """At night stale data is normal."""
    assert not is_stuck([stale()], -3, SETTINGS)


def test_not_stuck_when_sun_exactly_at_angle() -> None:
    """The sun must be above the angle, not merely at it."""
    assert not is_stuck([stale()], 5, SETTINGS)


def test_not_stuck_without_inverters() -> None:
    """A DTU without Inverters cannot be stuck."""
    assert not is_stuck([], 30, SETTINGS)


def test_one_fresh_inverter_means_not_stuck() -> None:
    """A single Inverter with fresh data proves the DTU is alive."""
    assert not is_stuck([stale(), fresh(), stale()], 30, SETTINGS)


@pytest.mark.parametrize(("age", "expected"), [(119, False), (120, True), (121, True)])
def test_staleness_boundary(age: float, expected: bool) -> None:
    """Data exactly as old as the staleness time no longer counts as fresh."""
    assert is_stuck([InverterObservation(age)], 30, SETTINGS) is expected


def test_inverters_that_are_not_pv_are_ignored() -> None:
    """Only PV Inverters count; without any the DTU is not stuck."""
    battery = InverterObservation(data_age=5000, is_pv=False)
    assert not is_stuck([battery], 30, SETTINGS)
    assert is_stuck([battery, stale()], 30, SETTINGS)
    assert not is_stuck([battery, fresh()], 30, SETTINGS)


def test_first_restart_is_immediate() -> None:
    """A stuck DTU is restarted at once."""
    policy = RestartPolicy(WAIT)
    verdict = policy.evaluate(T0, stuck=True)
    assert verdict.action is Action.RESTART
    assert not verdict.not_helping


def test_nothing_when_not_stuck() -> None:
    """A healthy DTU is left alone."""
    assert RestartPolicy(WAIT).evaluate(T0, stuck=False).action is Action.NOTHING


def test_nothing_during_wait_then_repeat() -> None:
    """After a restart nothing happens for the waiting time; then it repeats."""
    policy = RestartPolicy(WAIT)
    policy.evaluate(T0, stuck=True)
    policy.record_restart(T0)
    just_before = T0 + WAIT - timedelta(seconds=1)
    assert policy.evaluate(just_before, stuck=True).action is Action.NOTHING
    assert policy.evaluate(T0 + WAIT, stuck=True).action is Action.RESTART


def _restart_times(count: int) -> list[timedelta]:
    """Offsets from T0 at which the policy restarts a DTU that stays stuck."""
    policy = RestartPolicy(WAIT)
    offsets: list[timedelta] = []
    step = timedelta(seconds=10)
    elapsed = timedelta()
    while len(offsets) < count:
        if policy.evaluate(T0 + elapsed, stuck=True).action is Action.RESTART:
            policy.record_restart(T0 + elapsed)
            offsets.append(elapsed)
        elapsed += step
    return offsets


def test_wait_doubles_after_three_unsuccessful_restarts_with_cap() -> None:
    """Gaps are 3, 3, 3, then 6, 12, 24, 48 minutes, then capped at one hour."""
    offsets = _restart_times(10)
    gaps = [(b - a) // timedelta(minutes=1) for a, b in pairwise(offsets)]
    assert gaps == [3, 3, 3, 6, 12, 24, 48, 60, 60]


def test_not_helping_after_three_unsuccessful_restarts() -> None:
    """The alert holds once three restarts in a row have failed, not before."""
    policy = RestartPolicy(WAIT)
    now = T0
    flags = []
    for _ in range(5):
        verdict = policy.evaluate(now, stuck=True)
        assert verdict.action is Action.RESTART
        flags.append(verdict.not_helping)
        policy.record_restart(now)
        assert policy.evaluate(now, stuck=True).action is Action.NOTHING
        now += WAIT * 4
    assert flags == [False, False, False, True, True]


def test_alert_stays_during_wait() -> None:
    """The alert is reported on every evaluation until the DTU recovers."""
    policy = RestartPolicy(WAIT)
    now = T0
    for _ in range(4):
        policy.evaluate(now, stuck=True)
        policy.record_restart(now)
        now += timedelta(hours=1)
    policy.evaluate(now, stuck=True)
    policy.record_restart(now)
    assert policy.evaluate(now + timedelta(minutes=1), stuck=True).not_helping


def test_recovery_resets_counter_and_alert() -> None:
    """As soon as the DTU is not stuck, backoff and alert are gone."""
    policy = RestartPolicy(WAIT)
    now = T0
    for _ in range(4):
        policy.evaluate(now, stuck=True)
        policy.record_restart(now)
        now += timedelta(hours=1)
    assert policy.evaluate(now, stuck=True).not_helping
    assert not policy.evaluate(now, stuck=False).not_helping
    assert policy.unsuccessful_restarts == 0
    later = now + timedelta(hours=1)
    policy.evaluate(later, stuck=True)
    policy.record_restart(later)
    again = policy.evaluate(later + WAIT, stuck=True)
    assert again.action is Action.RESTART
    assert not again.not_helping


def test_manual_restart_starts_the_wait() -> None:
    """A restart by the owner postpones the next automatic one."""
    policy = RestartPolicy(WAIT)
    policy.evaluate(T0, stuck=False)
    policy.record_restart(T0)
    soon = T0 + timedelta(minutes=1)
    assert policy.evaluate(soon, stuck=True).action is Action.NOTHING
    assert policy.evaluate(T0 + WAIT, stuck=True).action is Action.RESTART


def test_manual_restart_of_healthy_dtu_is_not_unsuccessful() -> None:
    """A restart of a DTU that was fine does not count as a failure later."""
    policy = RestartPolicy(WAIT)
    policy.evaluate(T0, stuck=False)
    policy.record_restart(T0)
    policy.evaluate(T0 + WAIT, stuck=True)
    assert policy.unsuccessful_restarts == 0


def test_not_stuck_during_wait_changes_nothing() -> None:
    """A restarted DTU looks fresh; the policy draws no conclusion from it."""
    policy = RestartPolicy(WAIT)
    policy.evaluate(T0, stuck=True)
    policy.record_restart(T0)
    during = policy.evaluate(T0 + timedelta(minutes=1), stuck=False)
    assert during.action is Action.NOTHING
    assert policy.holding_stuck(T0 + timedelta(minutes=1))
    assert policy.evaluate(T0 + WAIT, stuck=True).action is Action.RESTART
    assert policy.unsuccessful_restarts == 1


def test_recovery_is_recognised_after_the_wait() -> None:
    """Not stuck at the first evaluation after the wait means recovered."""
    policy = RestartPolicy(WAIT)
    policy.evaluate(T0, stuck=True)
    policy.record_restart(T0)
    policy.evaluate(T0 + timedelta(minutes=1), stuck=False)
    after = policy.evaluate(T0 + WAIT, stuck=False)
    assert after.action is Action.NOTHING
    assert not policy.holding_stuck(T0 + WAIT)
    assert policy.unsuccessful_restarts == 0


def test_backoff_holds_when_every_restart_is_followed_by_looking_fresh() -> None:
    """Looking fresh in the middle of each wait does not reset the counting."""
    policy = RestartPolicy(WAIT)
    now = T0
    gaps = []
    last = None
    step = timedelta(seconds=10)
    for _ in range(4000):
        # Looks fresh for the first two minutes after a restart.
        stuck = not policy.waiting(now) or now - last >= timedelta(minutes=2)
        if policy.evaluate(now, stuck).action is Action.RESTART:
            policy.record_restart(now)
            if last is not None:
                gaps.append((now - last) // timedelta(minutes=1))
            last = now
        now += step
        if len(gaps) == 7:
            break
    assert gaps == [3, 3, 3, 6, 12, 24, 48]
    assert policy.evaluate(now, stuck=True).not_helping
