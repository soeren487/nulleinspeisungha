"""The learned conversion from irradiance to production, tested directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.nulleinspeisung.forecast_model import (
    ComponentFactors,
    Factors,
    HistoryRecord,
    IrradiancePoint,
    convert,
    history_days,
    is_usable,
    learn,
    participates,
    production_for,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def rec(
    day: int,
    hour: int,
    production: float,
    irradiance: float | None,
    curtailed: bool = False,
    minute: int = 0,
    month: int = 9,
    direct: float | None = None,
    diffuse: float | None = None,
) -> HistoryRecord:
    return HistoryRecord(
        datetime(2026, month, day, hour, minute, tzinfo=UTC),
        production,
        irradiance,
        curtailed,
        direct,
        diffuse,
    )


def full_days(count: int, per_day: int = 8, first_day: int = 1) -> list[HistoryRecord]:
    """``count`` September days, each with ``per_day`` usable records."""
    return [
        rec(first_day + d, 8 + i // 4, 100.0, 200.0, minute=i % 4 * 15)
        for d in range(count)
        for i in range(per_day)
    ]


def test_the_factor_of_an_hour_is_production_over_irradiance() -> None:
    """Uncurtailed records give one factor per UTC hour."""
    records = [
        rec(1, 10, 300.0, 500.0),
        rec(1, 10, 300.0, 500.0, minute=15),
        rec(1, 11, 400.0, 400.0),
    ]
    factors = learn(records, NOW)
    assert factors is not None
    assert factors.by_hour[10] == pytest.approx(0.6)
    assert factors.by_hour[11] == pytest.approx(1.0)
    assert set(factors.by_hour) == {10, 11}


def test_a_factor_is_a_ratio_of_sums_not_a_mean_of_ratios() -> None:
    """A bright quarter-hour counts for more than a dim one."""
    factors = learn([rec(1, 10, 100.0, 100.0), rec(1, 10, 600.0, 1000.0)], NOW)
    assert factors is not None
    assert factors.by_hour[10] == pytest.approx(700 / 1100, rel=1e-3)


def test_the_hour_is_the_utc_hour() -> None:
    """A record at 22:30 UTC belongs to hour 22, whatever the local time."""
    factors = learn([rec(1, 22, 50.0, 100.0, minute=30)], NOW)
    assert factors is not None
    assert list(factors.by_hour) == [22]


def test_curtailed_records_are_ignored() -> None:
    """A curtailed quarter-hour says what the House was allowed, not what it could."""
    records = [
        rec(1, 10, 300.0, 500.0),
        rec(1, 10, 20.0, 500.0, curtailed=True, minute=15),
        rec(1, 12, 10.0, 500.0, curtailed=True),
    ]
    factors = learn(records, NOW)
    assert factors is not None
    assert factors.by_hour == {10: pytest.approx(0.6)}
    assert factors.overall == pytest.approx(0.6)
    assert not participates(records[1])


def test_records_below_20_irradiance_are_ignored() -> None:
    """Dusk and dawn values are too noisy; 20 W/m² itself still counts."""
    records = [
        rec(1, 10, 300.0, 500.0),
        rec(1, 10, 5.0, 19.9, minute=15),
        rec(1, 10, 0.0, 0.0, minute=30),
        rec(1, 6, 10.0, 20.0),
    ]
    factors = learn(records, NOW)
    assert factors is not None
    assert factors.by_hour[10] == pytest.approx(0.6)
    assert factors.by_hour[6] == pytest.approx(0.5)


def test_records_without_irradiance_are_ignored() -> None:
    assert learn([rec(1, 10, 100.0, None)], NOW) is None
    assert not participates(rec(1, 10, 100.0, None))


def test_there_is_no_factor_without_participating_records() -> None:
    """Empty history, or nothing but curtailed and dim records, gives no factor."""
    assert learn([], NOW) is None
    assert learn([rec(1, 10, 5.0, 10.0), rec(1, 10, 5.0, 500.0, True)], NOW) is None


def test_recent_records_weigh_more() -> None:
    """Fourteen days older means half the weight."""
    now = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
    old = rec(1, 10, 200.0, 400.0)  # factor 0.5, 28 days old
    mid = rec(15, 10, 400.0, 400.0)  # factor 1.0, 14 days old
    fresh = rec(29, 10, 100.0, 100.0)  # factor 1.0, no age
    only_two = learn([mid, fresh], now)
    assert only_two is not None
    # weights 0.5 and 1.0 -> (0.5*400 + 100) / (0.5*400 + 100) = 1.0
    assert only_two.by_hour[10] == pytest.approx(1.0)
    all_three = learn([old, mid, fresh], now)
    assert all_three is not None
    # weights 0.25, 0.5, 1.0
    expected = (0.25 * 200 + 0.5 * 400 + 1.0 * 100) / (0.25 * 400 + 0.5 * 400 + 100)
    assert all_three.by_hour[10] == pytest.approx(expected)
    # Without weighting it would be 700/900.
    assert all_three.by_hour[10] != pytest.approx(700 / 900, abs=0.01)


def test_the_weight_halves_every_14_days() -> None:
    """Two records of equal irradiance, 14 days apart, weigh 1 : 1/2."""
    new = rec(14, 10, 100.0, 100.0)
    old = rec(1, 10, 400.0, 100.0)
    now = new.start
    factors = learn([old, new], now + timedelta(0))
    assert factors is not None
    # old is 13 days old: weight 0.5**(13/14)
    w = 0.5 ** (13 / 14)
    assert factors.by_hour[10] == pytest.approx((w * 400 + 100) / (w * 100 + 100))


def test_an_hour_without_records_uses_the_factor_over_all_records() -> None:
    """The fallback is the ratio of the sums over all participating records."""
    records = [rec(1, 10, 300.0, 500.0), rec(1, 11, 400.0, 400.0)]
    factors = learn(records, NOW)
    assert factors is not None
    assert factors.for_hour(10) == pytest.approx(0.6)
    assert factors.for_hour(11) == pytest.approx(1.0)
    assert factors.for_hour(15) == factors.overall == pytest.approx(700 / 900, rel=1e-3)


def test_production_is_the_hours_factor_times_irradiance() -> None:
    factors = Factors({10: 0.6, 11: 1.0}, 0.8)
    at_10 = datetime(2026, 10, 4, 10, 45, tzinfo=UTC)
    assert production_for(factors, at_10, 500.0) == pytest.approx(300.0)
    assert production_for(factors, at_10 + timedelta(hours=1), 500.0) == 500.0
    assert production_for(factors, at_10 + timedelta(hours=3), 500.0) == 400.0


def test_zero_irradiance_gives_zero_and_it_is_never_negative() -> None:
    factors = Factors({10: 0.6}, 0.8)
    start = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert production_for(factors, start, 0.0) == 0.0
    assert production_for(factors, start, -3.0) == 0.0
    assert production_for(Factors({10: -0.2}, -0.2), start, 100.0) == 0.0


def test_convert_makes_a_sorted_slot_per_irradiance_point() -> None:
    factors = Factors({10: 0.5}, 1.0)
    a = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    slots = convert(
        factors,
        [
            IrradiancePoint(a + timedelta(minutes=15), 100.0),
            IrradiancePoint(a, 400.0),
            IrradiancePoint(a + timedelta(hours=1), 0.0),
        ],
    )
    assert [(s.start, s.watts) for s in slots] == [
        (a, 200.0),
        (a + timedelta(minutes=15), 50.0),
        (a + timedelta(hours=1), 0.0),
    ]


def test_learned_factors_never_go_negative() -> None:
    factors = learn([rec(1, 10, -50.0, 500.0)], NOW)
    assert factors is not None
    assert factors.by_hour[10] == 0.0
    assert factors.overall == 0.0


def test_fourteen_days_with_eight_records_are_usable() -> None:
    records = full_days(14)
    assert history_days(records) == 14
    assert is_usable(records)


def test_thirteen_days_are_not_usable() -> None:
    records = full_days(13)
    assert history_days(records) == 13
    assert not is_usable(records)


def test_days_with_fewer_than_eight_records_do_not_count() -> None:
    """Fourteen days, one of them with seven records: thirteen count."""
    records = full_days(13) + full_days(1, per_day=7, first_day=20)
    assert history_days(records) == 13
    assert not is_usable(records)
    assert is_usable([*records, rec(20, 12, 100.0, 200.0)])


def test_curtailed_and_dim_records_do_not_count_towards_a_day() -> None:
    day = full_days(14)
    day[0] = rec(1, 8, 100.0, 200.0, curtailed=True)
    assert history_days(day) == 13
    day[0] = rec(1, 8, 100.0, 19.0)
    assert history_days(day) == 13


def test_days_are_utc_dates() -> None:
    """Records spread over two UTC dates count for neither when each has seven."""
    a = [rec(1, 22, 1.0, 100.0, minute=m) for m in (0, 15, 30, 45)]
    a += [rec(1, 23, 1.0, 100.0, minute=m) for m in (0, 15, 30)]
    b = [rec(2, 0, 1.0, 100.0, minute=m) for m in (0, 15, 30, 45)]
    assert history_days(a + b) == 0
    assert history_days([*a, rec(1, 23, 1.0, 100.0, minute=45)]) == 1


# -- direct and diffuse -------------------------------------------------------

A_DIRECT, A_DIFFUSE = 1.2, 0.4


def split_records(
    count: int = 30,
    direct_factor: float = A_DIRECT,
    diffuse_factor: float = A_DIFFUSE,
    hour: int = 10,
) -> list[HistoryRecord]:
    """``count`` records of one hour made from known coefficients, with components
    that vary independently of each other."""
    records = []
    for i in range(count):
        direct = 20.0 + 37.0 * ((i * 7) % 11)
        diffuse = 30.0 + 23.0 * ((i * 5) % 7)
        records.append(
            rec(
                1 + i // 4,
                hour,
                direct_factor * direct + diffuse_factor * diffuse,
                direct + diffuse,
                minute=i % 4 * 15,
                direct=direct,
                diffuse=diffuse,
            )
        )
    return records


def test_known_coefficients_are_recovered_from_the_components() -> None:
    factors = learn(split_records(), NOW)
    assert factors is not None
    pair = factors.split_by_hour[10]
    assert pair.direct == pytest.approx(A_DIRECT)
    assert pair.diffuse == pytest.approx(A_DIFFUSE)


def test_the_single_factors_are_learned_exactly_as_without_components() -> None:
    with_parts = split_records()
    without = [
        rec(r.start.day, 10, r.production, r.irradiance, minute=r.start.minute)
        for r in with_parts
    ]
    a, b = learn(with_parts, NOW), learn(without, NOW)
    assert a is not None
    assert b is not None
    assert a.by_hour == b.by_hour
    assert a.overall == b.overall
    assert b.split_by_hour == {}


def test_the_pair_is_used_when_the_point_has_both_components() -> None:
    factors = learn(split_records(), NOW)
    assert factors is not None
    at = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert production_for(factors, at, 300.0, 200.0, 100.0) == pytest.approx(
        A_DIRECT * 200.0 + A_DIFFUSE * 100.0
    )


def test_a_point_without_components_uses_the_single_factor() -> None:
    factors = learn(split_records(), NOW)
    assert factors is not None
    at = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    single = factors.by_hour[10] * 300.0
    assert production_for(factors, at, 300.0) == pytest.approx(single)
    assert production_for(factors, at, 300.0, 200.0, None) == pytest.approx(single)
    assert production_for(factors, at, 300.0, None, 100.0) == pytest.approx(single)


def test_an_hour_without_a_pair_uses_the_single_factor_with_components_given() -> None:
    factors = Factors({10: 0.5}, 0.7, {11: ComponentFactors(1.0, 2.0)})
    at = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert production_for(factors, at, 300.0, 200.0, 100.0) == pytest.approx(150.0)


def test_the_pair_never_gives_a_negative_production() -> None:
    factors = Factors({}, 1.0, {10: ComponentFactors(-1.0, -1.0)})
    at = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    assert production_for(factors, at, 300.0, 200.0, 100.0) == 0.0
    assert production_for(factors, at, 0.0, 200.0, 100.0) == 0.0


def test_convert_uses_the_components_of_each_point() -> None:
    factors = Factors({10: 0.5}, 0.5, {10: ComponentFactors(1.0, 0.0)})
    a = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    slots = convert(
        factors,
        [
            IrradiancePoint(a, 400.0, 300.0, 100.0),
            IrradiancePoint(a + timedelta(minutes=15), 400.0),
        ],
    )
    assert [s.watts for s in slots] == [300.0, 200.0]


def test_too_few_records_fall_back_to_the_single_factor() -> None:
    factors = learn(split_records(count=19), NOW)
    assert factors is not None
    assert factors.split_by_hour == {}
    assert 10 in factors.by_hour
    assert learn(split_records(count=20), NOW).split_by_hour.keys() == {10}


def test_proportional_components_fall_back_to_the_single_factor() -> None:
    """Direct always a third of the diffuse light: the two cannot be told apart."""
    records = []
    for i in range(30):
        diffuse = 50.0 + 10.0 * (i % 9)
        direct = diffuse / 3
        records.append(
            rec(
                1 + i // 4,
                10,
                0.9 * direct + 0.5 * diffuse,
                direct + diffuse,
                minute=i % 4 * 15,
                direct=direct,
                diffuse=diffuse,
            )
        )
    factors = learn(records, NOW)
    assert factors is not None
    assert factors.split_by_hour == {}


def test_records_without_components_still_feed_the_single_factor() -> None:
    old = [rec(1 + i // 4, 10, 300.0, 500.0, minute=i % 4 * 15) for i in range(40)]
    factors = learn(old + split_records(count=10), NOW)
    assert factors is not None
    assert factors.split_by_hour == {}
    assert factors.by_hour[10] > 0
    assert learn(old, NOW).by_hour[10] == pytest.approx(0.6)
    assert factors.by_hour[10] != pytest.approx(0.6)


def test_only_records_with_both_components_count_towards_the_minimum() -> None:
    old = [rec(1 + i // 4, 10, 300.0, 500.0, minute=i % 4 * 15) for i in range(40)]
    halves = [
        rec(
            r.start.day,
            10,
            r.production,
            r.irradiance,
            minute=r.start.minute,
            direct=r.direct,
            diffuse=None,
        )
        for r in split_records(count=30)
    ]
    factors = learn(old + halves, NOW)
    assert factors is not None
    assert factors.split_by_hour == {}


def test_curtailed_records_do_not_take_part_in_the_fit() -> None:
    records = split_records(count=30)
    records[:11] = [
        HistoryRecord(r.start, 0.0, r.irradiance, True, r.direct, r.diffuse)
        for r in records[:11]
    ]
    assert learn(records, NOW).split_by_hour == {}


def test_a_negative_direct_coefficient_is_set_to_zero_and_diffuse_refitted() -> None:
    """Production that falls with direct light: direct 0, diffuse fitted alone."""
    records = split_records(direct_factor=-0.3, diffuse_factor=1.0)
    pair = learn(records, NOW).split_by_hour[10]
    assert pair.direct == 0.0
    assert pair.diffuse > 0.0
    sff = sum(r.diffuse**2 for r in records)
    spf = sum(r.production * r.diffuse for r in records)
    assert pair.diffuse == pytest.approx(spf / sff, rel=0.01)


def test_a_negative_diffuse_coefficient_is_set_to_zero_and_direct_refitted() -> None:
    records = split_records(direct_factor=1.0, diffuse_factor=-0.3)
    pair = learn(records, NOW).split_by_hour[10]
    assert pair.diffuse == 0.0
    sdd = sum(r.direct**2 for r in records)
    spd = sum(r.production * r.direct for r in records)
    assert pair.direct == pytest.approx(spd / sdd, rel=0.01)


def test_recent_records_weigh_more_in_the_fit() -> None:
    """Old records made from other coefficients pull the pair less than new ones."""
    old = [
        HistoryRecord(
            r.start - timedelta(days=28),
            0.5 * r.direct + 0.1 * r.diffuse,
            r.irradiance,
            False,
            r.direct,
            r.diffuse,
        )
        for r in split_records(count=30)
    ]
    pair = learn(old + split_records(count=30), NOW + timedelta(days=0)).split_by_hour[
        10
    ]
    # Old records are 28 days older (two half-lives) than records made 33 days ago.
    assert 0.5 < pair.direct < A_DIRECT
    assert 0.1 < pair.diffuse < A_DIFFUSE
