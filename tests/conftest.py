"""Shared test fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp.client_exceptions import ClientError

import custom_components  # noqa: F401

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "opendtu"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the test Home Assistant load integrations from custom_components."""


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

    def add(self, dtu: SimDtu) -> None:
        """Add a DTU to the network."""
        self.dtus[dtu.serial] = dtu

    def apply(self) -> None:
        """Register all current DTU state with the mock client."""
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
                "max_power": inverter.rated_power or 600,
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
