"""Setting an Inverter Limit through the DTU client."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from custom_components.nulleinspeisung.dtu_client import (
    DtuAuthError,
    DtuClient,
    DtuConnectionError,
)

URL_LIMIT = "http://opendtu.local/api/limit/config"


def _client(hass: HomeAssistant) -> DtuClient:
    return DtuClient(async_get_clientsession(hass), "opendtu.local", "secret")


async def test_set_limit_request(
    hass: HomeAssistant, aioclient_mock: MagicMock
) -> None:
    """The limit goes out as a form field with a relative non-persistent limit."""
    aioclient_mock.post(
        URL_LIMIT, json={"type": "success", "message": "Settings saved!"}
    )
    await _client(hass).async_set_limit("114183178036", 42)
    assert len(aioclient_mock.mock_calls) == 1
    method, url, body, headers = aioclient_mock.mock_calls[0]
    assert method == "POST"
    assert url == URL(URL_LIMIT)
    token = base64.b64encode(b"admin:secret").decode()
    assert headers["Authorization"] == f"Basic {token}"
    assert list(body) == ["data"]
    assert json.loads(body["data"]) == {
        "serial": "114183178036",
        "limit_type": 1,
        "limit_value": 42,
    }
    assert isinstance(json.loads(body["data"])["limit_value"], int)


async def test_percent_is_sent_as_a_whole_number(
    hass: HomeAssistant, aioclient_mock: MagicMock
) -> None:
    """Even a float percent goes out without decimals."""
    aioclient_mock.post(URL_LIMIT, json={"type": "success"})
    await _client(hass).async_set_limit("1", 33.0)  # type: ignore[arg-type]
    assert '"limit_value": 33}' in aioclient_mock.mock_calls[0][2]["data"]


def test_client_offers_no_way_to_choose_the_limit_type() -> None:
    """Only the serial and the percent are parameters."""
    import inspect

    parameters = inspect.signature(DtuClient.async_set_limit).parameters
    assert list(parameters) == ["self", "serial", "percent"]


@pytest.mark.parametrize(
    "reply",
    [
        {"json": {"type": "warning", "message": "No values found!"}},
        {"json": {"message": "no type"}},
        {"json": ["success"]},
        {"text": "not json"},
        {"text": ""},
        {"status": 500, "json": {"type": "success"}},
    ],
)
async def test_failure_replies_raise_connection_error(
    hass: HomeAssistant, aioclient_mock: MagicMock, reply: dict
) -> None:
    """Anything but a success reply is an error."""
    aioclient_mock.post(URL_LIMIT, **reply)
    with pytest.raises(DtuConnectionError):
        await _client(hass).async_set_limit("1", 50)


async def test_unreachable_dtu_raises_connection_error(
    hass: HomeAssistant, aioclient_mock: MagicMock
) -> None:
    """A network error is a connection error."""
    from aiohttp.client_exceptions import ClientError

    aioclient_mock.post(URL_LIMIT, exc=ClientError())
    with pytest.raises(DtuConnectionError):
        await _client(hass).async_set_limit("1", 50)


async def test_wrong_password_raises_auth_error(
    hass: HomeAssistant, aioclient_mock: MagicMock
) -> None:
    """HTTP 401 means the administrator password was refused."""
    aioclient_mock.post(URL_LIMIT, status=401)
    with pytest.raises(DtuAuthError):
        await _client(hass).async_set_limit("1", 50)
