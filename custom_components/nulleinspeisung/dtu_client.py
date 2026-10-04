"""Client for one OpenDTU, using Home Assistant's shared HTTP session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import aiohttp

from .const import DTU_REQUEST_TIMEOUT, DTU_USER
from .dtu_models import DtuIdentity, DtuSnapshot, InverterSnapshot

_LIMIT_RELATIVE_NON_PERSISTENT = 1
"""OpenDTU's ``limit_type`` for percent of rated power, held in Inverter RAM."""


class DtuConnectionError(Exception):
    """The DTU did not answer, or answered something unusable."""


class DtuAuthError(Exception):
    """The DTU refused the administrator password."""


@dataclass(frozen=True, slots=True)
class _DeviceInfo:
    """What a DTU knows about the make of an Inverter."""

    model: str
    rated_power: int


def normalize_base_url(address: str) -> str:
    """Turn what the owner typed into a base URL without trailing slash.

    A bare host or ``host:port`` is taken to be plain HTTP.
    """
    address = address.strip()
    if "://" not in address:
        address = f"http://{address}"
    return address.rstrip("/")


class DtuClient:
    """Reads one DTU, restarts it and sets non-persistent Inverter limits."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, password: str
    ) -> None:
        """Create a client for the DTU at ``base_url``."""
        self._session = session
        self.base_url = normalize_base_url(base_url)
        self._auth_headers = {
            "Authorization": aiohttp.encode_basic_auth(DTU_USER, password)
        }
        self._device_info: dict[str, _DeviceInfo] = {}

    async def async_identify(self) -> DtuIdentity:
        """Check address and password and return who the DTU is.

        Raises DtuConnectionError or DtuAuthError.
        """
        config = await self._get("/api/dtu/config", authenticated=True)
        system = await self._get("/api/system/status")
        try:
            return DtuIdentity(
                serial=str(config["serial"]), hostname=str(system["hostname"])
            )
        except (KeyError, TypeError) as err:
            raise DtuConnectionError("Unexpected answer from DTU") from err

    async def async_fetch_snapshot(self) -> DtuSnapshot:
        """Read the DTU and every Inverter, one request after the other.

        Raises DtuConnectionError.
        """
        summary = await self._get("/api/livedata/status")
        limits = await self._get("/api/limit/status")
        system = await self._get("/api/system/status")
        try:
            inverters: dict[str, InverterSnapshot] = {}
            for item in summary["inverters"]:
                serial = str(item["serial"])
                power = await self._async_read_power(serial)
                info = await self._async_device_info(serial)
                limit = limits.get(serial, {})
                rated = int(limit.get("max_power", 0)) or (
                    info.rated_power if info and info.rated_power else None
                )
                inverters[serial] = InverterSnapshot(
                    serial=serial,
                    name=str(item["name"]),
                    reachable=bool(item["reachable"]),
                    producing=bool(item["producing"]),
                    poll_enabled=bool(item["poll_enabled"]),
                    data_age=int(item["data_age"]),
                    power=power,
                    limit=float(item["limit_relative"]),
                    limit_set_status=str(limit.get("limit_set_status", "Unknown")),
                    rated_power=rated,
                    model=info.model if info else None,
                )
            return DtuSnapshot(
                hostname=str(system["hostname"]),
                firmware_version=str(system["git_hash"]),
                chip_model=str(system["chipmodel"]),
                uptime=int(system["uptime"]),
                inverters=inverters,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise DtuConnectionError("Unexpected answer from DTU") from err

    async def async_restart(self) -> None:
        """Restart the DTU.

        Raises DtuConnectionError or DtuAuthError.
        """
        await self._post("/api/maintenance/reboot", {"reboot": True})

    async def async_set_limit(self, serial: str, percent: int) -> None:
        """Give one Inverter a relative, non-persistent limit in whole percent.

        The limit lives in the Inverter's RAM and is lost on a restart of the
        Inverter. There is deliberately no way to ask for a persistent limit.

        Raises DtuConnectionError or DtuAuthError.
        """
        reply = await self._post(
            "/api/limit/config",
            {
                "serial": serial,
                "limit_type": _LIMIT_RELATIVE_NON_PERSISTENT,
                "limit_value": int(percent),
            },
        )
        if reply is None or reply.get("type") != "success":
            raise DtuConnectionError(f"DTU refused the limit: {reply}")

    async def _async_read_power(self, serial: str) -> float | None:
        """Sum the AC power over all channels of one Inverter.

        ``None`` when the DTU has no usable AC data for this Inverter.
        """
        detail = await self._get(f"/api/livedata/status?inv={serial}")
        try:
            channels = detail["inverters"][0]["AC"]
            return float(sum(channel["Power"]["v"] for channel in channels.values()))
        except KeyError, IndexError, TypeError, ValueError, AttributeError:
            return None

    async def _async_device_info(self, serial: str) -> _DeviceInfo | None:
        """Model and rated power of an Inverter, asked until the DTU knows."""
        if serial in self._device_info:
            return self._device_info[serial]
        data = await self._get(f"/api/devinfo/status?inv={serial}")
        if not data.get("valid_data"):
            return None
        info = _DeviceInfo(
            model=str(data["hw_model_name"]), rated_power=int(data["max_power"])
        )
        self._device_info[serial] = info
        return info

    async def _get(self, path: str, authenticated: bool = False) -> dict[str, Any]:
        """GET a JSON object from the DTU."""
        try:
            async with self._session.get(
                f"{self.base_url}{path}",
                headers=self._auth_headers if authenticated else None,
                timeout=aiohttp.ClientTimeout(total=DTU_REQUEST_TIMEOUT),
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise DtuAuthError("Wrong administrator password")
                if response.status != HTTPStatus.OK:
                    raise DtuConnectionError(f"DTU answered HTTP {response.status}")
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise DtuConnectionError(f"Cannot reach DTU: {err}") from err
        if not isinstance(data, dict):
            raise DtuConnectionError("Unexpected answer from DTU")
        return data

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST a command as OpenDTU expects it: JSON in a form field ``data``.

        Returns the JSON reply, ``None`` if the reply is not a JSON object.
        """
        try:
            async with self._session.post(
                f"{self.base_url}{path}",
                data={"data": json.dumps(payload)},
                headers=self._auth_headers,
                timeout=aiohttp.ClientTimeout(total=DTU_REQUEST_TIMEOUT),
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise DtuAuthError("Wrong administrator password")
                if response.status != HTTPStatus.OK:
                    raise DtuConnectionError(f"DTU answered HTTP {response.status}")
                try:
                    reply = await response.json(content_type=None)
                except ValueError:
                    return None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DtuConnectionError(f"Cannot reach DTU: {err}") from err
        return reply if isinstance(reply, dict) else None
