"""The time-weighted mean of Grid Power, tested directly."""

from __future__ import annotations

import pytest

from custom_components.nulleinspeisung.grid_average import (
    GridPowerWindow,
    window_mean,
)


def test_without_a_reading_there_is_no_mean() -> None:
    assert window_mean([], 100.0, 60.0) == (None, 0.0)


def test_a_steady_meter_has_its_value_as_mean() -> None:
    samples = [(float(t), 50.0) for t in range(0, 100, 2)]
    mean, coverage = window_mean(samples, 99.0, 60.0)
    assert mean == pytest.approx(50)
    assert coverage == pytest.approx(1)


def test_readings_are_weighted_by_how_long_they_held() -> None:
    """-100 for 20 s, then 200 for 40 s: the mean is (-2000 + 8000) / 60."""
    mean, coverage = window_mean([(40.0, -100.0), (60.0, 200.0)], 100.0, 60.0)
    assert mean == pytest.approx(100)
    assert coverage == pytest.approx(1)


def test_a_reading_that_started_before_the_window_counts_only_inside_it() -> None:
    mean, _ = window_mean([(0.0, 1000.0), (70.0, 0.0)], 100.0, 60.0)
    # 1000 W from 40 s to 70 s, 0 W from 70 s to 100 s.
    assert mean == pytest.approx(500)


def test_a_single_old_reading_covers_the_whole_window() -> None:
    """A value that holds until a new one arrives is the value of the window."""
    mean, coverage = window_mean([(0.0, 120.0)], 1000.0, 60.0)
    assert mean == pytest.approx(120)
    assert coverage == pytest.approx(1)


def test_a_gap_between_readings_holds_the_earlier_value() -> None:
    mean, coverage = window_mean([(0.0, 100.0), (50.0, 300.0)], 60.0, 60.0)
    assert mean == pytest.approx((100 * 50 + 300 * 10) / 60)
    assert coverage == pytest.approx(1)


def test_a_buffer_that_started_recently_covers_part_of_the_window() -> None:
    mean, coverage = window_mean([(80.0, 10.0), (90.0, 30.0)], 100.0, 60.0)
    assert coverage == pytest.approx(20 / 60)
    assert mean == pytest.approx((10 * 10 + 30 * 10) / 20)


def test_a_reading_at_this_very_moment_has_no_coverage() -> None:
    assert window_mean([(100.0, 77.0)], 100.0, 60.0) == (77.0, 0.0)


def test_readings_from_the_future_are_ignored() -> None:
    mean, _ = window_mean([(0.0, 10.0), (200.0, 999.0)], 100.0, 60.0)
    assert mean == pytest.approx(10)


def test_the_window_forgets_old_readings_but_keeps_the_one_that_still_holds() -> None:
    window = GridPowerWindow()
    for t in range(0, 300, 2):
        window.add(float(t), float(t), keep=60.0)
    assert len(window._samples) <= 33
    mean, coverage = window.mean(299.0, 60.0)
    assert coverage == pytest.approx(1)
    # Readings 240..298 held 2 s each, the last one 1 s: mean of t over 239..299.
    assert mean == pytest.approx(269.0, abs=1.0)


def test_clearing_starts_the_coverage_afresh() -> None:
    window = GridPowerWindow()
    window.add(0.0, 5.0, keep=60.0)
    window.clear()
    assert window.mean(100.0, 60.0) == (None, 0.0)
    window.add(95.0, 5.0, keep=60.0)
    assert window.mean(100.0, 60.0)[1] == pytest.approx(5 / 60)


def test_a_reading_with_an_older_time_is_ignored_and_the_same_time_replaces() -> None:
    window = GridPowerWindow()
    window.add(10.0, 1.0, keep=60.0)
    window.add(5.0, 99.0, keep=60.0)
    window.add(10.0, 2.0, keep=60.0)
    assert window.mean(20.0, 10.0)[0] == pytest.approx(2.0)
