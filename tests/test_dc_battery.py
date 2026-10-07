"""The DC Batteries, calculated directly: stored energy, usual output and support."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import State

from custom_components.nulleinspeisung.dc_battery import (
    CONVERSION_FACTOR,
    StoredEnergy,
    dc_battery_support,
    house_stored_energy,
    sensor_kwh,
    stored_energy,
    stored_energy_by_inverter,
    usual_output,
)
from custom_components.nulleinspeisung.quarter_recorder import QuarterRecord

BERLIN = ZoneInfo("Europe/Berlin")
SLOT = timedelta(minutes=15)


def sensor(value: str, unit: str | None = None, device_class: str | None = None):
    attributes = {}
    if unit is not None:
        attributes["unit_of_measurement"] = unit
    if device_class is not None:
        attributes["device_class"] = device_class
    return State("sensor.x", value, attributes)


# -- stored energy ----------------------------------------------------------------


def test_wh_and_kwh_are_converted_to_kwh() -> None:
    assert sensor_kwh(sensor("1600", "Wh")) == pytest.approx(1.6)
    assert sensor_kwh(sensor("1.6", "kWh")) == pytest.approx(1.6)


def test_several_sensors_of_one_inverter_are_summed() -> None:
    states = {
        "sensor.a": sensor("1600", "Wh"),
        "sensor.b": sensor("0.8", "kWh"),
    }
    result = stored_energy(["sensor.a", "sensor.b"], states.get)
    assert result == StoredEnergy(pytest.approx(2.4), 2, 2)


@pytest.mark.parametrize("value", ["unavailable", "unknown", "full", "nan", "inf", ""])
def test_a_sensor_without_a_number_counts_as_not_readable(value: str) -> None:
    states = {"sensor.a": sensor("1600", "Wh"), "sensor.b": sensor(value, "Wh")}
    result = stored_energy(["sensor.a", "sensor.b", "sensor.gone"], states.get)
    assert result == StoredEnergy(pytest.approx(1.6), 1, 3)


def test_a_missing_sensor_contributes_nothing() -> None:
    assert stored_energy(["sensor.gone"], {}.get) == StoredEnergy(0.0, 0, 1)
    assert stored_energy([], {}.get) == StoredEnergy(0.0, 0, 0)


def test_an_unknown_unit_counts_as_wh_for_an_energy_class_only() -> None:
    assert sensor_kwh(sensor("1600", "units", "energy_storage")) == pytest.approx(1.6)
    assert sensor_kwh(sensor("1600", None, "energy")) == pytest.approx(1.6)
    assert sensor_kwh(sensor("1600", "units")) is None
    assert sensor_kwh(sensor("1600")) is None
    assert sensor_kwh(sensor("80", "%", "battery")) is None


def test_a_known_unit_wins_over_the_device_class() -> None:
    assert sensor_kwh(sensor("2", "kWh", "energy_storage")) == pytest.approx(2.0)


def test_an_ignored_sensor_is_configured_but_not_readable() -> None:
    states = {"sensor.a": sensor("80", "%", "battery")}
    assert stored_energy(["sensor.a"], states.get) == StoredEnergy(0.0, 0, 1)


def test_a_negative_value_counts_as_empty() -> None:
    assert sensor_kwh(sensor("-5", "Wh")) == 0.0


def test_stored_energy_per_inverter_and_for_the_house() -> None:
    states = {
        "sensor.a1": sensor("1600", "Wh"),
        "sensor.a2": sensor("unavailable", "Wh"),
        "sensor.b": sensor("0.5", "kWh"),
    }
    per_inverter = stored_energy_by_inverter(
        {"A": ["sensor.a1", "sensor.a2"], "B": ["sensor.b"], "C": []}, states.get
    )
    assert per_inverter == {
        "A": StoredEnergy(pytest.approx(1.6), 1, 2),
        "B": StoredEnergy(pytest.approx(0.5), 1, 1),
    }
    assert house_stored_energy(per_inverter) == StoredEnergy(pytest.approx(2.1), 2, 3)
    assert house_stored_energy({}) == StoredEnergy(0.0, 0, 0)


# -- usual output -----------------------------------------------------------------


def record(day: datetime, slot: int, watts: float) -> QuarterRecord:
    return QuarterRecord(day + slot * SLOT, watts)


def days(count: int, first: datetime | None = None) -> list[datetime]:
    first = first or datetime(2026, 10, 1, tzinfo=BERLIN)
    return [first + timedelta(days=i) for i in range(count)]


def test_the_usual_output_is_the_80th_percentile() -> None:
    values = [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    records = [
        record(day, 88, w)  # 22:00 local
        for day, w in zip(days(len(values)), values, strict=True)
    ]
    assert usual_output(records, BERLIN)[88] == pytest.approx(800.0)


def test_the_percentile_interpolates() -> None:
    records = [
        record(day, 0, w) for day, w in zip(days(4), (0, 10, 20, 30), strict=True)
    ]
    # position 0.8 * 3 = 2.4 between 20 and 30
    assert usual_output(records, BERLIN)[0] == pytest.approx(24.0)


def test_fewer_than_three_days_give_zero() -> None:
    two = [record(day, 88, 300.0) for day in days(2)]
    three = [record(day, 89, 300.0) for day in days(3)]
    profile = usual_output(two + three, BERLIN)
    assert profile[88] == 0.0
    assert profile[89] == pytest.approx(300.0)
    assert profile[10] == 0.0  # never recorded
    assert len(profile) == 96


def test_a_few_empty_nights_do_not_pull_a_fixed_output_down() -> None:
    values = [0.0, 0.0, 0.0, 0.0] + [300.0] * 17  # 4 of 21 nights with empty batteries
    records = [record(day, 92, w) for day, w in zip(days(21), values, strict=True)]
    assert usual_output(records, BERLIN)[92] == pytest.approx(300.0)


def test_many_empty_nights_do_pull_it_down() -> None:
    values = [0.0] * 18 + [300.0] * 3
    records = [record(day, 92, w) for day, w in zip(days(21), values, strict=True)]
    assert usual_output(records, BERLIN)[92] == 0.0


def test_the_slot_follows_local_time_across_a_daylight_saving_change() -> None:
    """21:00 local is 19:00 UTC in summer time and 20:00 UTC in winter time."""
    summer = datetime(2026, 10, 24, 19, 0, tzinfo=UTC)  # 21:00 CEST
    winter = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)  # 21:00 CET
    records = [QuarterRecord(summer + timedelta(days=i), 300.0) for i in range(2)] + [
        QuarterRecord(winter + timedelta(days=i), 300.0) for i in range(2)
    ]
    profile = usual_output(records, BERLIN)
    assert profile[84] == pytest.approx(300.0)  # 21:00 is slot 84
    assert profile[80] == 0.0
    assert profile[83] == 0.0


# -- support ------------------------------------------------------------------------

NOW = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)  # 21:00 CEST
DEADLINE = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # 06:00 CEST


def flat(watts: float = 300.0) -> dict[int, float]:
    """The same output in every quarter-hour of the day."""
    return dict.fromkeys(range(96), watts)


def night_only(watts: float = 300.0) -> dict[int, float]:
    """The output from 21:00 to 06:00 local time."""
    return {s: watts if s >= 84 or s < 24 else 0.0 for s in range(96)}


def test_the_factor_is_85_percent() -> None:
    assert CONVERSION_FACTOR == 0.85


def test_the_support_follows_the_usual_output_while_energy_lasts() -> None:
    result = dc_battery_support(NOW, DEADLINE, {"A": night_only()}, {"A": 10.0}, BERLIN)
    assert result.kwh == pytest.approx(0.3 * 9)
    assert len(result.watts) == 36
    assert set(result.watts.values()) == {300.0}


def test_it_stops_when_the_stored_energy_times_the_factor_is_used_up() -> None:
    # 1.6 kWh * 0.85 = 1.36 kWh = 4.533 h of 300 W: 18 full quarters and 0.133 of one
    result = dc_battery_support(NOW, DEADLINE, {"A": flat()}, {"A": 1.6}, BERLIN)
    assert result.kwh == pytest.approx(1.36)
    assert len(result.watts) == 19
    first_quarters = [result.watts[NOW + i * SLOT] for i in range(18)]
    assert first_quarters == [300.0] * 18


def test_the_last_quarter_hour_is_partial() -> None:
    # 0.4 kWh * 0.85 = 0.34 kWh: 4 quarters of 300 W (0.3 kWh) and 0.04 kWh
    result = dc_battery_support(NOW, DEADLINE, {"A": flat()}, {"A": 0.4}, BERLIN)
    assert result.kwh == pytest.approx(0.34)
    assert result.watts[NOW + 4 * SLOT] == pytest.approx(0.04 * 1000 / 0.25)
    assert NOW + 5 * SLOT not in result.watts


def test_the_current_quarter_hour_counts_with_its_remaining_fraction() -> None:
    now = NOW + timedelta(minutes=5)  # 10 minutes of the quarter-hour are left
    result = dc_battery_support(now, DEADLINE, {"A": flat()}, {"A": 0.4}, BERLIN)
    first = result.watts[NOW]
    assert first == pytest.approx(300.0)  # the mean over the part that is left
    assert result.kwh == pytest.approx(0.34)
    # 0.05 kWh in the first, then 0.075 kWh each: three more full, the fifth partial
    assert NOW + 4 * SLOT in result.watts
    assert NOW + 5 * SLOT not in result.watts


def test_nothing_is_counted_after_the_deadline() -> None:
    deadline = NOW + timedelta(hours=1)
    result = dc_battery_support(NOW, deadline, {"A": flat()}, {"A": 10.0}, BERLIN)
    assert result.kwh == pytest.approx(0.3)
    assert max(result.watts) == deadline - SLOT
    late = dc_battery_support(DEADLINE, DEADLINE, {"A": flat()}, {"A": 10.0}, BERLIN)
    assert late.kwh == 0.0
    assert not late.watts
    after = dc_battery_support(
        DEADLINE + SLOT, DEADLINE, {"A": flat()}, {"A": 10.0}, BERLIN
    )
    assert after.kwh == 0.0


def test_a_deadline_inside_a_quarter_hour_counts_its_part() -> None:
    deadline = NOW + timedelta(minutes=20)
    result = dc_battery_support(NOW, deadline, {"A": flat()}, {"A": 10.0}, BERLIN)
    assert result.kwh == pytest.approx(0.3 * 20 / 60)
    assert result.watts[NOW + SLOT] == pytest.approx(300.0)


def test_the_support_of_several_inverters_is_summed() -> None:
    result = dc_battery_support(
        NOW,
        DEADLINE,
        {"A": flat(300.0), "B": flat(200.0)},
        {"A": 0.4, "B": 10.0},
        BERLIN,
    )
    # A gives 0.34 kWh and stops after 4.53 quarters, B gives 0.2 kW all night
    assert result.watts[NOW] == pytest.approx(500.0)
    assert result.watts[NOW + 10 * SLOT] == pytest.approx(200.0)
    assert result.kwh == pytest.approx(0.34 + 0.2 * 9)


def test_an_inverter_without_stored_energy_gives_no_support() -> None:
    for stored in ({}, {"A": 0.0}, {"B": 5.0}):
        result = dc_battery_support(NOW, DEADLINE, {"A": flat()}, stored, BERLIN)
        assert result.kwh == 0.0
        assert not result.watts


def test_without_any_inverter_there_is_no_support() -> None:
    result = dc_battery_support(NOW, DEADLINE, {}, {}, BERLIN)
    assert result.kwh == 0.0
    assert not result.watts


def test_a_quarter_hour_without_usual_output_gives_nothing() -> None:
    result = dc_battery_support(NOW, DEADLINE, {"A": {}}, {"A": 5.0}, BERLIN)
    assert result.kwh == 0.0


def test_the_support_never_exceeds_the_stored_energy() -> None:
    for stored in (0.1, 0.5, 1.6, 3.2):
        result = dc_battery_support(
            NOW, DEADLINE, {"A": flat(900.0)}, {"A": stored}, BERLIN
        )
        assert result.kwh <= stored
        assert result.kwh == pytest.approx(stored * 0.85)


def test_the_usual_output_is_read_in_local_time() -> None:
    # 21:00 CEST is slot 84; an output only in slot 84 gives 300 W * 0.25 h
    result = dc_battery_support(NOW, DEADLINE, {"A": {84: 300.0}}, {"A": 5.0}, BERLIN)
    assert list(result.watts) == [NOW]
    assert result.kwh == pytest.approx(0.075)
