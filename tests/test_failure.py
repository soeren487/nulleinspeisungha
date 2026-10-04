"""The failure decision of a House, tested directly with the time passed in."""

from __future__ import annotations

from custom_components.nulleinspeisung.failure import FailureWatch

INTERVAL = 15.0


def test_a_healthy_meter_is_no_failure() -> None:
    """A known Grid Power that has just been reported is fine."""
    watch = FailureWatch()
    assessment = watch.assess(1000, INTERVAL, -200.0, 1000, True)
    assert not assessment.failed
    assert watch.since is None


def test_unknown_grid_power_is_no_failure_before_three_intervals() -> None:
    """Missing values for less than three Update Intervals are not yet a failure."""
    watch = FailureWatch()
    watch.assess(1000, INTERVAL, 100.0, 1000, True)
    for now in (1015, 1030, 1044):
        assert not watch.assess(now, INTERVAL, None, 1000, True).grid_meter


def test_unknown_grid_power_is_a_failure_after_three_intervals() -> None:
    """Three Update Intervals without a value make the Grid Meter failed."""
    watch = FailureWatch()
    watch.assess(1000, INTERVAL, 100.0, 1000, True)
    assessment = watch.assess(1045, INTERVAL, None, 1000, True)
    assert assessment.grid_meter
    assert assessment.failed
    assert watch.since == 1045
    # The failure keeps its start while it lasts.
    watch.assess(1060, INTERVAL, None, 1000, True)
    assert watch.since == 1045


def test_the_grace_follows_the_update_interval() -> None:
    """Three longer intervals are a longer wait."""
    watch = FailureWatch()
    watch.assess(1000, 30.0, 100.0, 1000, True)
    assert not watch.assess(1089, 30.0, None, 1000, True).failed
    assert watch.assess(1090, 30.0, None, 1000, True).failed


def test_a_meter_that_never_had_a_value_is_counted_from_the_first_run() -> None:
    """With no reading yet, the wait starts when the control first looks."""
    watch = FailureWatch()
    assert not watch.assess(1000, INTERVAL, None, None, True).failed
    assert not watch.assess(1044, INTERVAL, None, None, True).failed
    assert watch.assess(1045, INTERVAL, None, None, True).grid_meter


def test_a_sensor_that_stopped_reporting_is_a_failure() -> None:
    """A value that is still known but was last reported long ago is stale."""
    watch = FailureWatch()
    assert not watch.assess(1030, INTERVAL, -50.0, 1000, True).failed
    assert not watch.assess(1044, INTERVAL, -50.0, 1000, True).failed
    assert watch.assess(1045, INTERVAL, -50.0, 1000, True).grid_meter


def test_a_sensor_that_repeats_its_value_is_healthy() -> None:
    """Reporting the same value again counts as reporting."""
    watch = FailureWatch()
    for step in range(20):
        now = 1000 + step * INTERVAL
        assert not watch.assess(now, INTERVAL, -50.0, now, True).failed


def test_recovery_ends_the_failure() -> None:
    """A fresh value clears the failure and its start."""
    watch = FailureWatch()
    watch.assess(1000, INTERVAL, 100.0, 1000, True)
    assert watch.assess(1100, INTERVAL, None, 1000, True).failed
    assessment = watch.assess(1115, INTERVAL, 100.0, 1115, True)
    assert not assessment.failed
    assert watch.since is None


def test_reset_forgets_the_past() -> None:
    """After a reset the wait starts afresh."""
    watch = FailureWatch()
    watch.assess(1000, INTERVAL, 100.0, 1000, True)
    watch.reset()
    assert not watch.assess(5000, INTERVAL, None, None, True).failed


def test_no_dtu_answering_is_a_failure_after_three_intervals() -> None:
    """When nothing can be controlled for three intervals, the House has failed."""
    watch = FailureWatch()
    assert not watch.assess(1000, INTERVAL, 0.0, 1000, True).failed
    assert not watch.assess(1015, INTERVAL, 0.0, 1015, False).failed
    assessment = watch.assess(1060, INTERVAL, 0.0, 1060, False)
    assert assessment.dtus
    assert not assessment.grid_meter


def test_one_of_two_dtus_answering_is_no_failure() -> None:
    """The caller reports whether any DTU answers; one is enough."""
    watch = FailureWatch()
    for step in range(10):
        now = 1000 + step * INTERVAL
        assert not watch.assess(now, INTERVAL, 0.0, now, True).failed


def test_a_house_without_dtus_to_ask_is_never_failed_by_them() -> None:
    """``None`` means there is nothing to ask."""
    watch = FailureWatch()
    for step in range(10):
        now = 1000 + step * INTERVAL
        assert not watch.assess(now, INTERVAL, 0.0, now, None).dtus


def test_both_failures_are_reported_together() -> None:
    """Meter and DTUs can fail at the same time."""
    watch = FailureWatch()
    watch.assess(1000, INTERVAL, 0.0, 1000, True)
    watch.assess(1001, INTERVAL, 0.0, 1001, False)
    assessment = watch.assess(1100, INTERVAL, None, 1001, False)
    assert assessment.grid_meter
    assert assessment.dtus
