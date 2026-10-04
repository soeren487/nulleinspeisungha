"""Shared test fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.client_exceptions import ClientError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

import custom_components  # noqa: F401
from custom_components.nulleinspeisung.const import (
    CONF_BATTERY_BACKED,
    CONF_GRID_METER,
    CONF_GRID_METER_SIGN,
    CONF_INVERTERS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PASSWORD,
    CONF_URL,
    DOMAIN,
    SIGN_IMPORT,
    SUBENTRY_TYPE_DTU,
    SUBENTRY_TYPE_HOUSE,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "opendtu"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the test Home Assistant load integrations from custom_components."""


class Sun:
    """The sun's elevation as the integration sees it; tests move it."""

    def __init__(self) -> None:
        """Start with the sun well below the horizon."""
        self.elevation = -20.0


@pytest.fixture(autouse=True)
def sun() -> Iterator[Sun]:
    """Control the sun elevation at the single place the integration reads it."""
    sun = Sun()
    with patch(
        "custom_components.nulleinspeisung.solar.sun_elevation",
        side_effect=lambda hass: sun.elevation,
    ):
        yield sun


def load_fixture(filename: str) -> dict[str, Any]:
    """Load a JSON fixture file."""
    with open(FIXTURE_DIR / filename) as f:
        return json.load(f)


@dataclass
class SimInverter:
    """Simulated OpenDTU Inverter with customizable state."""

    serial: str
    name: str
    reachable: bool
    producing: bool
    poll_enabled: bool
    data_age: int
    power: float | None
    limit: float
    rated_power: int | None
    model: str | None
    devinfo_valid: bool = True
    detail_empty: bool = False


@dataclass
class SimDtu:
    """Simulated OpenDTU with customizable state."""

    base_url: str
    serial: str
    hostname: str
    firmware: str
    password_ok: bool = True
    down: bool = False
    reboot_fails: bool = False
    limit_fails: bool = False
    """Limit commands are answered with a warning and not applied."""
    reflect_limits: bool = True
    """An accepted limit shows up in the limit the DTU reports afterwards."""
    after_reboot: str | None = None
    """``"aging"``: every Inverter's data age is the time since the last reboot,
    as on a real DTU that cannot reach its Inverters. ``"fresh"``: they deliver
    fresh data. ``None``: the data ages are left as set."""
    rebooted_at: datetime | None = None
    reboots_seen: int = 0
    inverters: list[SimInverter] = field(default_factory=list)

    @classmethod
    def default(cls) -> SimDtu:
        """Create a DTU with the three recorded Inverters."""
        return cls(
            base_url="http://opendtu.local",
            serial="199980126212",
            hostname="OpenDTU-Buero",
            firmware="v26.3.30",
            inverters=[
                SimInverter(
                    serial="114183178036",
                    name="OmaOpa",
                    reachable=False,
                    producing=False,
                    poll_enabled=False,
                    data_age=5417,
                    power=0.0,
                    limit=100.0,
                    rated_power=600,
                    model="HM-600-4T",
                ),
                SimInverter(
                    serial="116191100801",
                    name="Büro 4",
                    reachable=False,
                    producing=False,
                    poll_enabled=False,
                    data_age=5142,
                    power=0.0,
                    limit=100.0,
                    rated_power=1500,
                    model="HM-1500-4T",
                ),
                SimInverter(
                    serial="1164a00ccd81",
                    name="Büro 5",
                    reachable=False,
                    producing=False,
                    poll_enabled=False,
                    data_age=4301,
                    power=0.0,
                    limit=100.0,
                    rated_power=1600,
                    model="HM-1600-2T",
                ),
            ],
        )


class DtuNetwork:
    """Manages a network of SimDtu instances with mock registration."""

    def __init__(self, aioclient_mock: MagicMock) -> None:
        """Initialize the network."""
        self.aioclient_mock = aioclient_mock
        self.dtus: dict[str, SimDtu] = {}
        self._post_log: dict[str, list[tuple[str, Any, Any]]] = {}

    def add(self, dtu: SimDtu) -> None:
        """Add a DTU to the network."""
        self.dtus[dtu.serial] = dtu

    def apply(self) -> None:
        """Register all current DTU state with the mock client."""
        self._keep_posts()
        self.aioclient_mock.clear_requests()

        for dtu in self.dtus.values():
            base_url = dtu.base_url.rstrip("/")

            if dtu.down:
                # Register all URLs to raise an error
                for path in [
                    "/api/livedata/status",
                    "/api/limit/status",
                    "/api/system/status",
                    "/api/dtu/config",
                ]:
                    self.aioclient_mock.get(f"{base_url}{path}", exc=ClientError())
                self.aioclient_mock.post(
                    f"{base_url}/api/maintenance/reboot", exc=ClientError()
                )
                self.aioclient_mock.post(
                    f"{base_url}/api/limit/config", exc=ClientError()
                )
                for inverter in dtu.inverters:
                    self.aioclient_mock.get(
                        f"{base_url}/api/livedata/status?inv={inverter.serial}",
                        exc=ClientError(),
                    )
                    self.aioclient_mock.get(
                        f"{base_url}/api/devinfo/status?inv={inverter.serial}",
                        exc=ClientError(),
                    )
                continue

            # Register detail and devinfo BEFORE generic endpoints
            for inverter in dtu.inverters:
                self._register_inverter_detail(base_url, inverter)
                self._register_devinfo(base_url, inverter)

            # Register generic endpoints
            self._register_livedata_status(base_url, dtu)
            self._register_limit_status(base_url, dtu)
            self._register_system_status(base_url, dtu)
            self._register_dtu_config(base_url, dtu)
            self.aioclient_mock.post(
                f"{base_url}/api/maintenance/reboot",
                status=500 if dtu.reboot_fails else 200,
                json={"type": "success", "message": "Reboot triggered!"},
            )
            self.aioclient_mock.post(
                f"{base_url}/api/limit/config",
                side_effect=lambda method, url, data, dtu=dtu: self._answer_limit(
                    dtu, method, url, data
                ),
            )

    def _keep_posts(self) -> None:
        """Move the POST requests seen so far into the log."""
        for dtu in self.dtus.values():
            prefix = dtu.base_url.rstrip("/")
            self._post_log.setdefault(dtu.serial, []).extend(
                (str(call[1])[len(prefix) :], call[2], call[3])
                for call in self.aioclient_mock.mock_calls
                if call[0] == "POST" and str(call[1]).startswith(prefix)
            )
        self.aioclient_mock.mock_calls.clear()

    def posts(self, serial: str, path: str) -> list[tuple[Any, Any]]:
        """All POST requests to a path of a DTU, as (form body, headers)."""
        self._keep_posts()
        return [
            (body, headers)
            for p, body, headers in self._post_log.get(serial, [])
            if p == path
        ]

    def reboots(self, serial: str = "199980126212") -> list[tuple[Any, Any]]:
        """All reboot requests sent to a DTU, as (body, headers)."""
        return self.posts(serial, "/api/maintenance/reboot")

    def limits(self, serial: str = "199980126212") -> list[tuple[str, int, int]]:
        """Limit commands sent to a DTU, as (inverter serial, type, value)."""
        commands = (
            json.loads(body["data"])
            for body, _ in self.posts(serial, "/api/limit/config")
        )
        return [(c["serial"], c["limit_type"], c["limit_value"]) for c in commands]

    def clear_limits(self) -> None:
        """Forget the limit commands seen so far."""
        self._keep_posts()
        for log in self._post_log.values():
            log[:] = [entry for entry in log if entry[0] != "/api/limit/config"]

    async def _answer_limit(
        self, dtu: SimDtu, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        """Answer a limit command; apply it unless the DTU is set to fail."""
        if dtu.limit_fails:
            return AiohttpClientMockResponse(
                method, url, json={"type": "warning", "message": "No values found!"}
            )
        command = json.loads(data["data"])
        for inverter in dtu.inverters:
            if inverter.serial == command["serial"] and dtu.reflect_limits:
                inverter.limit = float(command["limit_value"])
        if dtu.reflect_limits:
            self.apply()
        return AiohttpClientMockResponse(
            method, url, json={"type": "success", "message": "Settings saved!"}
        )

    def simulate(self) -> None:
        """Let DTUs that were rebooted since the last call age their data."""
        now = datetime.now(UTC)
        for dtu in self.dtus.values():
            seen = len(self.reboots(dtu.serial))
            if seen > dtu.reboots_seen:
                dtu.reboots_seen = seen
                dtu.rebooted_at = now
            if dtu.after_reboot is None or dtu.rebooted_at is None:
                continue
            age = (
                int((now - dtu.rebooted_at).total_seconds())
                if dtu.after_reboot == "aging"
                else 3
            )
            for inverter in dtu.inverters:
                inverter.data_age = age
        self.apply()

    def _register_livedata_status(self, base_url: str, dtu: SimDtu) -> None:
        """Register /api/livedata/status endpoint."""
        livedata = load_fixture("livedata_status.json")
        # Build the inverter list from simulator state
        inverters = []
        for i, inv in enumerate(dtu.inverters):
            inverters.append(
                {
                    "serial": inv.serial,
                    "name": inv.name,
                    "order": i,
                    "data_age": inv.data_age,
                    "data_age_ms": inv.data_age * 1000,
                    "poll_enabled": inv.poll_enabled,
                    "reachable": inv.reachable,
                    "producing": inv.producing,
                    "limit_relative": inv.limit,
                    "limit_absolute": int(inv.limit),
                    "radio_stats": {},
                }
            )
        livedata["inverters"] = inverters
        self.aioclient_mock.get(f"{base_url}/api/livedata/status", json=livedata)

    def _register_inverter_detail(self, base_url: str, inverter: SimInverter) -> None:
        """Register /api/livedata/status?inv=<serial> endpoint."""
        if inverter.detail_empty:
            self.aioclient_mock.get(
                f"{base_url}/api/livedata/status?inv={inverter.serial}",
                json={"inverters": []},
            )
            return

        fixture_data = load_fixture("livedata_status_inv.json")
        # Update power in AC section
        if inverter.power is not None:
            fixture_data["inverters"][0]["AC"]["0"]["Power"]["v"] = inverter.power
        self.aioclient_mock.get(
            f"{base_url}/api/livedata/status?inv={inverter.serial}",
            json=fixture_data,
        )

    def _register_devinfo(self, base_url: str, inverter: SimInverter) -> None:
        """Register /api/devinfo/status?inv=<serial> endpoint."""
        devinfo = load_fixture("devinfo_status.json")
        devinfo["valid_data"] = inverter.devinfo_valid
        if inverter.devinfo_valid and inverter.model:
            devinfo["hw_model_name"] = inverter.model
            devinfo["max_power"] = inverter.rated_power or 600

        self.aioclient_mock.get(
            f"{base_url}/api/devinfo/status?inv={inverter.serial}",
            json=devinfo,
        )

    def _register_limit_status(self, base_url: str, dtu: SimDtu) -> None:
        """Register /api/limit/status endpoint."""
        limit = load_fixture("limit_status.json")
        for inverter in dtu.inverters:
            limit[inverter.serial] = {
                "limit_relative": inverter.limit,
                "max_power": inverter.rated_power
                or (600 if inverter.devinfo_valid else 0),
                "limit_set_status": "Ok",
            }

        self.aioclient_mock.get(f"{base_url}/api/limit/status", json=limit)

    def _register_system_status(self, base_url: str, dtu: SimDtu) -> None:
        """Register /api/system/status endpoint."""
        system = load_fixture("system_status.json")
        system["hostname"] = dtu.hostname
        system["git_hash"] = dtu.firmware
        self.aioclient_mock.get(f"{base_url}/api/system/status", json=system)

    def _register_dtu_config(self, base_url: str, dtu: SimDtu) -> None:
        """Register /api/dtu/config endpoint (requires auth)."""
        if not dtu.password_ok:
            self.aioclient_mock.get(f"{base_url}/api/dtu/config", status=401)
            return

        config = load_fixture("dtu_config.json")
        config["serial"] = dtu.serial
        self.aioclient_mock.get(f"{base_url}/api/dtu/config", json=config)


@pytest.fixture
def dtu_network(aioclient_mock: MagicMock) -> DtuNetwork:
    """Provide a DTU network simulator."""
    return DtuNetwork(aioclient_mock)


@dataclass
class SimHouse:
    """A House as stored in its config subentry."""

    name: str
    grid_meter: str = "sensor.grid_meter"
    sign: str = SIGN_IMPORT
    inverters: list[str] = field(default_factory=list)
    battery_backed: list[str] = field(default_factory=list)
    unique_id: str | None = None

    def subentry_data(self) -> dict[str, Any]:
        """The subentry as ``MockConfigEntry`` takes it."""
        return {
            "data": {
                CONF_LATITUDE: 52.5,
                CONF_LONGITUDE: 13.4,
                CONF_GRID_METER: self.grid_meter,
                CONF_GRID_METER_SIGN: self.sign,
                CONF_INVERTERS: self.inverters,
                CONF_BATTERY_BACKED: self.battery_backed,
            },
            "unique_id": self.unique_id or f"house-{self.name.casefold()}",
            "title": self.name,
            "subentry_type": SUBENTRY_TYPE_HOUSE,
        }


async def setup_entry(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    *dtus: SimDtu,
    settings: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    houses: list[SimHouse] | None = None,
) -> MockConfigEntry:
    """Put the DTUs on the simulated network and set up an entry with them.

    ``houses`` are added as House subentries after the DTUs.
    """
    for dtu in dtus:
        dtu_network.add(dtu)
    dtu_network.apply()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options=options or {},
        subentries_data=[
            {
                "data": {
                    CONF_URL: dtu.base_url,
                    CONF_PASSWORD: "password",
                    **(settings or {}),
                },
                "unique_id": dtu.serial,
                "title": dtu.hostname,
                "subentry_type": SUBENTRY_TYPE_DTU,
            }
            for dtu in dtus
        ]
        + [house.subentry_data() for house in houses or []],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def second_dtu() -> SimDtu:
    """A second DTU with two Inverters whose serials differ from the default."""
    dtu = SimDtu.default()
    dtu.base_url = "http://opendtu-garage.local"
    dtu.serial = "199980126999"
    dtu.hostname = "OpenDTU-Garage"
    dtu.inverters = dtu.inverters[:2]
    dtu.inverters[0].serial = "200000000001"
    dtu.inverters[0].name = "Garage 1"
    dtu.inverters[1].serial = "200000000002"
    dtu.inverters[1].name = "Garage 2"
    return dtu
