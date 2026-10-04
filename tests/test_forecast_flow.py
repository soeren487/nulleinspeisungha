"""The PV Forecast of a House, end to end with a simulated Open-Meteo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.nulleinspeisung.const import DOMAIN
from tests.conftest import (
    DtuNetwork,
    SimDtu,
    SimHouse,
    SimOpenMeteo,
    seed_history,
    setup_entry,
)

OMA, BUERO4, BUERO5 = "114183178036", "116191100801", "1164a00ccd81"
HOUSE = "house-home"
FREEZE = datetime(2026, 10, 4, 10, 7, tzinfo=UTC)
FACTOR_10, FACTOR_11, FACTOR_OTHER = 0.6, 0.8, 0.7


def day_records(
    day: datetime, hours: tuple[int, ...] = (10, 11), curtailed: bool = False
) -> list[tuple[datetime, float, float | None, bool]]:
    """Eight records of a day: hour 10 learns 0.6, hour 11 learns 0.8."""
    factors = {10: FACTOR_10, 11: FACTOR_11}
    return [
        (
            day.replace(hour=hour, minute=minute),
            factors[hour] * 400.0,
            400.0,
            curtailed,
        )
        for hour in hours
        for minute in (0, 15, 30, 45)
    ]


def prepared_history(
    days: int = 14,
) -> list[tuple[datetime, float, float | None, bool]]:
    """``days`` full days before 2026-10-04, newest last."""
    first = datetime(2026, 10, 4, tzinfo=UTC) - timedelta(days=days)
    return [r for d in range(days) for r in day_records(first + timedelta(days=d))]


def dtu() -> SimDtu:
    """The default DTU, with the first two Inverters producing 500 W together."""
    dtu = SimDtu.default()
    for inverter, power in zip(dtu.inverters, (300.0, 200.0, 0.0), strict=True):
        inverter.reachable = True
        inverter.producing = power > 0
        inverter.power = power
    return dtu


def entity(hass: HomeAssistant, platform: str, key: str, house: str = HOUSE) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{house}_{key}"
    )
    assert entity_id is not None, f"no {platform} {key}"
    return entity_id


def state(hass: HomeAssistant, platform: str, key: str, house: str = HOUSE) -> str:
    return hass.states.get(entity(hass, platform, key, house)).state


def issue(hass: HomeAssistant, house: str = HOUSE) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"pv_forecast_unavailable_{house}"
    )


async def advance(hass: HomeAssistant, freezer, minutes: int, step: int = 5) -> None:
    """Let ``minutes`` pass, ``step`` minutes at a time."""
    for _ in range(minutes // step):
        freezer.tick(timedelta(minutes=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    freezer,
    history: list[tuple[datetime, float, float | None, bool]] | None = None,
    hass_storage: dict[str, Any] | None = None,
    house: SimHouse | None = None,
    at: datetime = FREEZE,
):
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(at)
    if history is not None:
        assert hass_storage is not None
        seed_history(hass_storage, "house-home", history)
    return await setup_entry(
        hass,
        dtu_network,
        dtu(),
        houses=[house or SimHouse("Home", inverters=[OMA, BUERO4])],
        open_meteo=open_meteo,
    )


def expected_kwh(open_meteo: SimOpenMeteo, first: datetime, last: datetime) -> float:
    """kWh of the recorded irradiance in [first, last) under the prepared factors."""
    total = 0.0
    for time, value in zip(open_meteo.times, open_meteo.irradiance, strict=True):
        start = datetime.fromtimestamp(time, UTC)
        if first <= start < last:
            factor = {10: FACTOR_10, 11: FACTOR_11}.get(start.hour, FACTOR_OTHER)
            total += factor * value * 0.25 / 1000
    return total


async def test_the_sensors_show_the_forecast_for_the_frozen_instant(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """At 10:07 UTC the House shows 0.6 x 482 W now and the day sums in kWh."""
    await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )

    assert float(state(hass, "sensor", "pv_forecast_power")) == pytest.approx(
        0.6 * 482.0
    )
    # Berlin is UTC+2 on 2026-10-04: today is 22:00Z on the 3rd to 22:00Z on the 4th.
    today = expected_kwh(
        open_meteo,
        datetime(2026, 10, 3, 22, tzinfo=UTC),
        datetime(2026, 10, 4, 22, tzinfo=UTC),
    )
    tomorrow = expected_kwh(
        open_meteo,
        datetime(2026, 10, 4, 22, tzinfo=UTC),
        datetime(2026, 10, 5, 22, tzinfo=UTC),
    )
    assert abs(float(state(hass, "sensor", "pv_forecast_today")) - today) < 0.001
    assert abs(float(state(hass, "sensor", "pv_forecast_tomorrow")) - tomorrow) < 0.001
    assert 1 < today < 4
    assert 1 < tomorrow < 4
    assert state(hass, "binary_sensor", "pv_forecast_usable") == "on"
    assert state(hass, "sensor", "pv_forecast_history_days") == "14"

    attributes = {
        key: hass.states.get(entity(hass, "sensor", key)).attributes
        for key in ("pv_forecast_power", "pv_forecast_today", "pv_forecast_tomorrow")
    }
    assert attributes["pv_forecast_power"]["unit_of_measurement"] == "W"
    assert attributes["pv_forecast_power"]["device_class"] == "power"
    for key in ("pv_forecast_today", "pv_forecast_tomorrow"):
        assert attributes[key]["unit_of_measurement"] == "kWh"
        assert attributes[key]["device_class"] == "energy"
    days = er.async_get(hass).async_get(
        entity(hass, "sensor", "pv_forecast_history_days")
    )
    assert days.entity_category is er.EntityCategory.DIAGNOSTIC


async def test_the_forecast_is_what_a_later_feature_reads(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """``house.forecast`` gives the usable flag and the future slots in W."""
    entry = await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )
    forecast = next(iter(entry.runtime_data.houses.values())).forecast
    assert forecast.usable
    assert forecast.history_days == 14
    future = forecast.future
    assert future[0].start == datetime(2026, 10, 4, 10, 15, tzinfo=UTC)
    assert future[0].watts == pytest.approx(0.6 * 497.0)
    assert future[-1].start == datetime(2026, 10, 5, 23, 45, tzinfo=UTC)
    assert len(future) == 192 - 41
    # Hour 13 has no records: the factor over all records, 0.7.
    at_13 = next(s for s in future if s.start.hour == 13 and s.start.minute == 0)
    assert at_13.watts == pytest.approx(
        FACTOR_OTHER * open_meteo.irradiance_at(at_13.start), rel=1e-3
    )
    assert all(s.watts >= 0 for s in future)
    night = next(s for s in future if s.start.hour == 2)
    assert night.watts == 0.0


async def test_an_unusable_forecast_still_shows_the_current_factors(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """With 13 days the forecast is not usable, but the sensors follow the factors."""
    await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(13), hass_storage
    )
    assert state(hass, "binary_sensor", "pv_forecast_usable") == "off"
    assert state(hass, "sensor", "pv_forecast_history_days") == "13"
    assert float(state(hass, "sensor", "pv_forecast_power")) == pytest.approx(
        0.6 * 482.0
    )
    assert float(state(hass, "sensor", "pv_forecast_today")) > 1


async def test_without_any_history_the_forecast_is_unknown(
    hass: HomeAssistant, dtu_network: DtuNetwork, open_meteo: SimOpenMeteo, freezer
) -> None:
    """There is no factor to apply, so the three forecast sensors are unknown."""
    await setup(hass, dtu_network, open_meteo, freezer)
    for key in ("pv_forecast_power", "pv_forecast_today", "pv_forecast_tomorrow"):
        assert state(hass, "sensor", key) == "unknown"
    assert state(hass, "binary_sensor", "pv_forecast_usable") == "off"
    assert state(hass, "sensor", "pv_forecast_history_days") == "0"


async def test_curtailed_days_teach_nothing(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """Fourteen days that were all curtailed give no factor and no usability."""
    first = datetime(2026, 9, 20, tzinfo=UTC)
    history = [
        r
        for d in range(14)
        for r in day_records(first + timedelta(days=d), curtailed=True)
    ]
    await setup(hass, dtu_network, open_meteo, freezer, history, hass_storage)
    assert state(hass, "sensor", "pv_forecast_power") == "unknown"
    assert state(hass, "binary_sensor", "pv_forecast_usable") == "off"
    assert state(hass, "sensor", "pv_forecast_history_days") == "0"


async def test_the_request_names_the_houses_location(
    hass: HomeAssistant, dtu_network: DtuNetwork, open_meteo: SimOpenMeteo, freezer
) -> None:
    """Setup asks once, for the House's coordinates, as this integration."""
    await setup(hass, dtu_network, open_meteo, freezer)
    [(query, headers)] = open_meteo.requests
    assert (query["latitude"], query["longitude"]) == ("52.5", "13.4")
    assert headers["User-Agent"].startswith("nulleinspeisung/")


async def test_the_irradiance_is_fetched_again_every_hour(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """No request before the hour is up; then a new forecast replaces the old."""
    await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )
    assert len(open_meteo.requests) == 1
    await advance(hass, freezer, 55)
    assert len(open_meteo.requests) == 1
    open_meteo.irradiance[41] = 1000.0  # 10:15Z
    await advance(hass, freezer, 5)
    assert len(open_meteo.requests) == 2
    # At 11:07Z the 11:00 slot is current: 0.8 x 366... check the 10:15 one changed.
    forecast = None
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    forecast = next(iter(entry.runtime_data.houses.values())).forecast
    slot = next(
        s for s in forecast.slots if s.start.hour == 10 and s.start.minute == 15
    )
    assert slot.watts == pytest.approx(0.6 * 1000.0)
    await advance(hass, freezer, 60)
    assert len(open_meteo.requests) == 3


async def test_a_failed_fetch_keeps_the_old_data_and_retries_after_ten_minutes(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """The sensors keep showing the last forecast; the next try is 10 minutes later."""
    await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )
    today = state(hass, "sensor", "pv_forecast_today")
    open_meteo.down = True
    await advance(hass, freezer, 60)
    assert len(open_meteo.requests) == 2
    assert state(hass, "sensor", "pv_forecast_today") == today
    await advance(hass, freezer, 5)
    assert len(open_meteo.requests) == 2
    await advance(hass, freezer, 5)
    assert len(open_meteo.requests) == 3
    await advance(hass, freezer, 10)
    assert len(open_meteo.requests) == 4
    assert state(hass, "sensor", "pv_forecast_today") == today
    assert issue(hass) is None

    open_meteo.down = False
    open_meteo.irradiance[:] = [0.0] * 192
    await advance(hass, freezer, 10)
    assert len(open_meteo.requests) == 5
    assert float(state(hass, "sensor", "pv_forecast_today")) == 0.0
    await advance(hass, freezer, 60)
    assert len(open_meteo.requests) == 6


async def test_a_repair_issue_after_six_hours_without_a_forecast(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """After more than 6 hours since the last success the owner sees a repair issue."""
    await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )
    open_meteo.down = True
    await advance(hass, freezer, 355)
    assert issue(hass) is None
    await advance(hass, freezer, 10)
    found = issue(hass)
    assert found is not None
    assert found.translation_key == "pv_forecast_unavailable"
    assert found.translation_placeholders == {"house": "Home"}
    assert found.severity is ir.IssueSeverity.ERROR
    assert not found.is_fixable

    open_meteo.down = False
    await advance(hass, freezer, 10)
    assert issue(hass) is None


async def test_the_repair_issue_is_raised_even_if_the_first_fetch_never_worked(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    freezer,
) -> None:
    """Counting from the start of the integration when there never was a success."""
    open_meteo.down = True
    await setup(hass, dtu_network, open_meteo, freezer)
    await advance(hass, freezer, 355)
    assert issue(hass) is None
    await advance(hass, freezer, 10)
    assert issue(hass) is not None


async def test_the_entry_loads_when_open_meteo_is_down_at_setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    hass_storage,
    freezer,
) -> None:
    """The entry is loaded, the sensors are unknown, and the forecast arrives later."""
    open_meteo.down = True
    entry = await setup(
        hass, dtu_network, open_meteo, freezer, prepared_history(), hass_storage
    )
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor", "pv_forecast_power") == "unknown"
    assert state(hass, "sensor", "pv_forecast_today") == "unknown"
    assert state(hass, "binary_sensor", "pv_forecast_usable") == "on"

    open_meteo.down = False
    await advance(hass, freezer, 10)
    # 10:17 UTC: the slot of 10:15.
    assert float(state(hass, "sensor", "pv_forecast_power")) == pytest.approx(
        0.6 * 497.0
    )
    assert float(state(hass, "sensor", "pv_forecast_today")) > 1


async def test_an_error_answer_is_handled_like_a_failure(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    open_meteo: SimOpenMeteo,
    freezer,
) -> None:
    """Open-Meteo rejecting the request does not stop the entry either."""
    open_meteo.error = True
    entry = await setup(hass, dtu_network, open_meteo, freezer)
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor", "pv_forecast_today") == "unknown"


async def test_a_house_without_pv_inverters_has_no_forecast(
    hass: HomeAssistant, dtu_network: DtuNetwork, open_meteo: SimOpenMeteo, freezer
) -> None:
    """Only Battery-backed Inverters (or none): no entities, no request."""
    house = SimHouse("Home", inverters=[OMA], battery_backed=[OMA])
    entry = await setup(hass, dtu_network, open_meteo, freezer, house=house)
    assert next(iter(entry.runtime_data.houses.values())).forecast is None
    registry = er.async_get(hass)
    for platform, key in (
        ("sensor", "pv_forecast_power"),
        ("sensor", "pv_forecast_today"),
        ("sensor", "pv_forecast_tomorrow"),
        ("sensor", "pv_forecast_history_days"),
        ("binary_sensor", "pv_forecast_usable"),
    ):
        assert registry.async_get_entity_id(platform, DOMAIN, f"{HOUSE}_{key}") is None
    assert open_meteo.requests == []

    empty = await setup(hass, dtu_network, open_meteo, freezer, house=SimHouse("Home"))
    assert empty.state is ConfigEntryState.LOADED
    assert open_meteo.requests == []
