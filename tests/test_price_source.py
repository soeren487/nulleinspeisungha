"""Tests of the price source: the Tibber client and the helpers on its results."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from aiohttp.client_exceptions import ClientError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.nulleinspeisung.const import TIBBER_URL
from custom_components.nulleinspeisung.price_source import (
    PriceLevel,
    PricePoint,
    TibberAuthError,
    TibberConnectionError,
    TibberHome,
    async_create_client,
    current_price,
    known_until,
)
from tests.conftest import (
    HOME_1,
    TIBBER_TOKEN,
    SimTibber,
    load_tibber_fixture,
    recorded_price_info,
)

BERLIN = ZoneInfo("Europe/Berlin")
MANIFEST = (
    Path(__file__).parent.parent / "custom_components/nulleinspeisung/manifest.json"
)


async def test_homes_request_is_exact_and_homes_are_read(
    hass: HomeAssistant, tibber: SimTibber, aioclient_mock
) -> None:
    """The homes query goes to Tibber with bearer token and user agent."""
    client = await async_create_client(hass, TIBBER_TOKEN)
    homes = await client.async_homes()

    assert homes == [
        TibberHome("00000000-0000-4000-8000-000000000001", "Home 1"),
        TibberHome("00000000-0000-4000-8000-000000000002", "Home 2"),
    ]
    [(method, url, body, headers)] = aioclient_mock.mock_calls
    assert method == "POST"
    assert str(url) == TIBBER_URL == "https://api.tibber.com/v1-beta/gql"
    assert body == {"query": "{ viewer { homes { id appNickname timeZone } } }"}
    assert headers["Authorization"] == f"Bearer {TIBBER_TOKEN}"
    manifest = json.loads(MANIFEST.read_text())
    assert headers["User-Agent"] == f"nulleinspeisung/{manifest['version']}"


async def test_price_request_is_exact(
    hass: HomeAssistant, tibber: SimTibber, aioclient_mock
) -> None:
    """The price query asks for quarter-hour prices of today and tomorrow."""
    client = await async_create_client(hass, TIBBER_TOKEN)
    await client.async_prices(HOME_1)

    [(method, url, body, headers)] = aioclient_mock.mock_calls
    assert method == "POST"
    assert str(url) == TIBBER_URL
    assert body == {
        "query": (
            f'{{ viewer {{ home(id: "{HOME_1}") {{ currentSubscription {{ '
            "priceInfo(resolution: QUARTER_HOURLY) { "
            "today { total startsAt currency level } "
            "tomorrow { total startsAt currency level } "
            "} } } } }"
        )
    }
    assert headers["Authorization"] == f"Bearer {TIBBER_TOKEN}"
    assert headers["User-Agent"].startswith("nulleinspeisung/")


async def test_recorded_prices_are_parsed(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """192 prices, today then tomorrow, with aware starts and Price Levels."""
    client = await async_create_client(hass, TIBBER_TOKEN)
    prices = await client.async_prices(HOME_1)

    assert len(prices) == 192
    assert [p.start for p in prices] == sorted(p.start for p in prices)
    assert all(p.start.tzinfo is not None for p in prices)
    first, last = prices[0], prices[-1]
    assert first == PricePoint(
        start=datetime(2026, 10, 4, 0, 0, tzinfo=BERLIN),
        total=0.3797,
        currency="EUR",
        level=PriceLevel.NORMAL,
    )
    assert first.start.utcoffset() == timedelta(hours=2)
    assert last.start == datetime(2026, 10, 5, 23, 45, tzinfo=BERLIN)
    assert prices[95].start == datetime(2026, 10, 4, 23, 45, tzinfo=BERLIN)
    assert prices[96].start == datetime(2026, 10, 5, 0, 0, tzinfo=BERLIN)
    assert {p.level for p in prices} == {
        PriceLevel.VERY_CHEAP,
        PriceLevel.CHEAP,
        PriceLevel.NORMAL,
        PriceLevel.EXPENSIVE,
    }
    assert {p.currency for p in prices} == {"EUR"}
    recorded = recorded_price_info()["today"][40]
    assert prices[40].total == recorded["total"] == 0.3284
    assert prices[40].level is PriceLevel.NORMAL


async def test_levels_are_lower_case_keys() -> None:
    """The five levels have lower-case keys."""
    assert [level.value for level in PriceLevel] == [
        "very_cheap",
        "cheap",
        "normal",
        "expensive",
        "very_expensive",
    ]


async def test_tomorrow_not_yet_published_gives_only_today(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """An empty ``tomorrow`` leaves 96 prices."""
    tibber.published = False
    client = await async_create_client(hass, TIBBER_TOKEN)
    prices = await client.async_prices(HOME_1)
    assert len(prices) == 96
    assert prices[-1].start == datetime(2026, 10, 4, 23, 45, tzinfo=BERLIN)


async def test_invalid_token_is_an_auth_error(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """HTTP 200 with UNAUTHENTICATED means the token was refused."""
    client = await async_create_client(hass, "wrong")
    with pytest.raises(TibberAuthError):
        await client.async_homes()
    with pytest.raises(TibberAuthError):
        await client.async_prices(HOME_1)


async def test_unknown_home_is_a_connection_error(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """HOME_NOT_FOUND is not an authentication problem."""
    client = await async_create_client(hass, TIBBER_TOKEN)
    with pytest.raises(TibberConnectionError):
        await client.async_prices("00000000-0000-4000-8000-0000000000ff")


async def test_http_failure_is_a_connection_error(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """A server error and an unreachable host both count as connection errors."""
    client = await async_create_client(hass, TIBBER_TOKEN)
    tibber.http_status = 503
    with pytest.raises(TibberConnectionError):
        await client.async_prices(HOME_1)
    tibber.http_status = 200
    tibber.down = True
    with pytest.raises(TibberConnectionError):
        await client.async_prices(HOME_1)
    with pytest.raises(TibberConnectionError):
        await client.async_homes()


@pytest.mark.parametrize(
    "answer",
    [
        {"data": {"viewer": {"nothing": True}}},
        {"data": None},
        {"data": {"viewer": {"home": {"currentSubscription": None}}}},
        ["not", "an", "object"],
        {
            "data": {
                "viewer": {
                    "home": {
                        "currentSubscription": {
                            "priceInfo": {
                                "today": [
                                    {
                                        "total": 0.3,
                                        "startsAt": "2026-10-04T00:00:00.000",
                                        "currency": "EUR",
                                        "level": "NORMAL",
                                    }
                                ],
                                "tomorrow": [],
                            }
                        }
                    }
                }
            }
        },
        {
            "data": {
                "viewer": {
                    "home": {
                        "currentSubscription": {
                            "priceInfo": {
                                "today": [
                                    {
                                        "total": 0.3,
                                        "startsAt": "2026-10-04T00:00:00.000+02:00",
                                        "currency": "EUR",
                                        "level": "SURPRISING",
                                    }
                                ],
                                "tomorrow": [],
                            }
                        }
                    }
                }
            }
        },
        {"errors": [{"message": "boom", "extensions": {"code": "INTERNAL"}}]},
    ],
)
async def test_malformed_answers_are_connection_errors(
    hass: HomeAssistant, aioclient_mock, answer
) -> None:
    """An answer that is not a usable price list is a connection error."""
    aioclient_mock.post(TIBBER_URL, json=answer)
    client = await async_create_client(hass, TIBBER_TOKEN)
    with pytest.raises(TibberConnectionError):
        await client.async_prices(HOME_1)


async def test_answer_that_is_not_json_is_a_connection_error(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """HTML from a proxy is not a price list."""
    aioclient_mock.post(TIBBER_URL, text="<html>maintenance</html>")
    client = await async_create_client(hass, TIBBER_TOKEN)
    with pytest.raises(TibberConnectionError):
        await client.async_homes()


async def test_unauthorized_fixture_is_what_the_simulator_sends(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """The recorded answer for an invalid token is understood as such."""
    aioclient_mock.post(TIBBER_URL, json=load_tibber_fixture("unauthorized.json"))
    client = await async_create_client(hass, TIBBER_TOKEN)
    with pytest.raises(TibberAuthError):
        await client.async_homes()
    assert AiohttpClientMockResponse  # imported for the shared style


# -- helpers --------------------------------------------------------------


def day(date: tuple[int, int, int], step: timedelta = timedelta(minutes=15)):
    """All slots of one local day in Berlin, built by walking real time."""
    year, month, d = date
    start = datetime(year, month, d, tzinfo=BERLIN)
    points = []
    moment = start.astimezone(ZoneInfo("UTC"))
    while moment.astimezone(BERLIN).date() == start.date():
        points.append(
            PricePoint(
                start=moment.astimezone(BERLIN),
                total=0.1 + len(points) / 1000,
                currency="EUR",
                level=PriceLevel.NORMAL,
            )
        )
        moment += step
    return points


@pytest.mark.parametrize(
    ("date", "slots"),
    [((2026, 10, 4), 96), ((2027, 3, 28), 92), ((2026, 10, 25), 100)],
)
def test_current_price_at_slot_boundaries(date, slots) -> None:
    """Each slot is valid from its start to just before the next, on any day."""
    prices = day(date)
    assert len(prices) == slots
    for i, point in enumerate(prices):
        # Real time, because wall-clock sums are ambiguous in the repeated hour.
        begin = point.start.astimezone(UTC)
        assert current_price(prices, point.start) is point
        assert current_price(prices, begin + timedelta(seconds=1)) is point
        just_before_end = begin + timedelta(minutes=14, seconds=59)
        assert current_price(prices, just_before_end) is point
        if i + 1 < slots:
            assert current_price(prices, prices[i + 1].start) is prices[i + 1]


@pytest.mark.parametrize("date", [(2026, 10, 4), (2027, 3, 28), (2026, 10, 25)])
def test_no_price_before_the_first_or_after_the_last_slot(date) -> None:
    """The instants outside the known prices have no price."""
    prices = day(date)
    first = prices[0].start.astimezone(UTC)
    assert current_price(prices, first - timedelta(seconds=1)) is None
    end = prices[-1].start.astimezone(UTC) + timedelta(minutes=15)
    assert current_price(prices, end) is None
    assert current_price(prices, end + timedelta(hours=3)) is None
    assert known_until(prices) == end


def test_no_prices_means_no_current_price_and_no_end() -> None:
    """With nothing known, nothing is valid."""
    assert current_price([], datetime(2026, 10, 4, 10, tzinfo=BERLIN)) is None
    assert known_until([]) is None


def test_slot_length_comes_from_the_data() -> None:
    """Hourly prices are valid for an hour, not for a quarter-hour."""
    hourly = day((2026, 10, 4), step=timedelta(hours=1))
    assert len(hourly) == 24
    assert current_price(hourly, hourly[3].start + timedelta(minutes=50)) is hourly[3]
    assert current_price(hourly, hourly[3].start + timedelta(hours=1)) is hourly[4]
    assert known_until(hourly) == hourly[-1].start + timedelta(hours=1)


def test_a_gap_between_two_days_has_no_price() -> None:
    """Prices for today and the day after tomorrow leave tomorrow without price."""
    prices = day((2026, 10, 4)) + day((2026, 10, 6))
    assert current_price(prices, datetime(2026, 10, 5, 12, tzinfo=BERLIN)) is None
    assert current_price(prices, datetime(2026, 10, 6, 0, 5, tzinfo=BERLIN)) is not None


def test_client_error_is_a_plain_exception_type() -> None:
    """Guards the import used by the simulator."""
    assert issubclass(ClientError, Exception)
