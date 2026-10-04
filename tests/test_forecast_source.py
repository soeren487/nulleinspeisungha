"""The Open-Meteo client against the simulated service at the HTTP boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.nulleinspeisung.forecast_source import (
    OpenMeteoClient,
    OpenMeteoError,
    async_create_client,
    parse_irradiance,
)
from tests.conftest import SimOpenMeteo, load_open_meteo_fixture

MANIFEST = json.loads(
    (
        Path(__file__).parent.parent / "custom_components/nulleinspeisung/manifest.json"
    ).read_text()
)


@pytest.fixture
def client(hass: HomeAssistant, open_meteo: SimOpenMeteo) -> OpenMeteoClient:
    return OpenMeteoClient(async_get_clientsession(hass), "nulleinspeisung/test")


async def test_the_request_asks_for_two_utc_days_of_quarter_hours(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    """The request names the location, the quarter-hourly radiation and UTC times."""
    await client.async_irradiance(51.0, 9.0)
    [(query, headers)] = open_meteo.requests
    assert query == {
        "latitude": "51.0",
        "longitude": "9.0",
        "minutely_15": "shortwave_radiation",
        "forecast_days": "2",
        "timeformat": "unixtime",
        "timezone": "UTC",
    }
    assert headers["User-Agent"] == "nulleinspeisung/test"
    assert "Authorization" not in headers


async def test_the_user_agent_names_the_integration_and_its_version(
    hass: HomeAssistant, open_meteo: SimOpenMeteo
) -> None:
    """The client of the integration identifies itself with the manifest version."""
    created = await async_create_client(hass)
    await created.async_irradiance(51.0, 9.0)
    [(_, headers)] = open_meteo.requests
    assert headers["User-Agent"] == f"nulleinspeisung/{MANIFEST['version']}"


async def test_the_recorded_answer_is_parsed_in_order(
    client: OpenMeteoClient,
) -> None:
    """192 points, UTC-aware, at 15-minute steps, with the recorded values."""
    points = await client.async_irradiance(51.0, 9.0)
    recorded = load_open_meteo_fixture("forecast.json")["minutely_15"]
    assert len(points) == 192
    assert points[0].start == datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
    assert points[0].start.utcoffset().total_seconds() == 0
    assert points[-1].start == datetime(2026, 10, 5, 23, 45, tzinfo=UTC)
    assert [p.irradiance for p in points] == recorded["shortwave_radiation"]
    assert [int(p.start.timestamp()) for p in points] == recorded["time"]
    assert points[42].start == datetime(2026, 10, 4, 10, 30, tzinfo=UTC)
    assert points[42].irradiance == 526.0


async def test_points_are_sorted_by_start(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    """An answer in a different order still gives points sorted by start."""
    block = open_meteo.answer["minutely_15"]
    block["time"].reverse()
    block["shortwave_radiation"].reverse()
    points = await client.async_irradiance(51.0, 9.0)
    assert [p.start for p in points] == sorted(p.start for p in points)
    assert points[0].irradiance == 0.0
    assert points[42].irradiance == 526.0


async def test_null_values_are_skipped(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    """A quarter-hour without a value gives no point."""
    open_meteo.irradiance[42] = None
    open_meteo.irradiance[43] = None
    points = await client.async_irradiance(51.0, 9.0)
    assert len(points) == 190
    starts = {p.start for p in points}
    assert datetime(2026, 10, 4, 10, 30, tzinfo=UTC) not in starts
    assert datetime(2026, 10, 4, 10, 45, tzinfo=UTC) not in starts
    assert datetime(2026, 10, 4, 10, 15, tzinfo=UTC) in starts


async def test_http_400_with_an_error_body_is_an_error(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    """Invalid input is answered with HTTP 400; that is the one error class."""
    open_meteo.error = True
    with pytest.raises(OpenMeteoError):
        await client.async_irradiance(951.0, 9.0)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_other_http_failures_are_errors(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo, status: int
) -> None:
    open_meteo.http_status = status
    with pytest.raises(OpenMeteoError):
        await client.async_irradiance(51.0, 9.0)


async def test_an_unreachable_service_is_an_error(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    open_meteo.down = True
    with pytest.raises(OpenMeteoError):
        await client.async_irradiance(51.0, 9.0)


async def test_a_malformed_answer_is_an_error(
    client: OpenMeteoClient, open_meteo: SimOpenMeteo
) -> None:
    open_meteo.malformed = True
    with pytest.raises(OpenMeteoError):
        await client.async_irradiance(51.0, 9.0)


def test_an_error_flag_in_a_200_answer_is_an_error() -> None:
    """``error: true`` is an error whatever the status."""
    with pytest.raises(OpenMeteoError, match="Latitude"):
        parse_irradiance(load_open_meteo_fixture("error.json"))


@pytest.mark.parametrize(
    "answer",
    [
        None,
        [],
        {},
        {"minutely_15": None},
        {"minutely_15": {"time": [1], "shortwave_radiation": []}},
        {"minutely_15": {"time": ["x"], "shortwave_radiation": [1.0]}},
        {"minutely_15": {"time": [1], "shortwave_radiation": ["x"]}},
        {"minutely_15": {"time": [1]}},
    ],
)
def test_malformed_answers_raise_the_one_error(answer: object) -> None:
    with pytest.raises(OpenMeteoError):
        parse_irradiance(answer)


def test_the_simulator_is_registered_for_the_mock(aioclient_mock: MagicMock) -> None:
    """Guards the helper: the simulator answers any location."""
    assert (
        SimOpenMeteo(aioclient_mock).irradiance_at(
            datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
        )
        == 482.0
    )
