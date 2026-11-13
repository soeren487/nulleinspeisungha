"""The diagnostics of the entry contain the plan and nothing that identifies the
installation."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.nulleinspeisung.const import CONF_NOTIFY_TARGET
from custom_components.nulleinspeisung.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import (
    HOME_1,
    TIBBER_TOKEN,
    DtuNetwork,
    SimGx,
    SimHouse,
    SimTibber,
    setup_entry,
)
from tests.test_discharge_block_flow import TWO_CHEAP
from tests.test_grid_charging_flow import (
    BUERO4,
    OMA,
    _clock,
    _dtu,
    _grid,
    _items,
)

SECRETS = {
    "host": "gx-secret-host.example",
    "portal": "secretportal123",
    "topic": "secret/topic/grid",
    "notify": "notify.secret_phone",
    "dtu_url": "http://opendtu.local",
    "dtu_host": "OpenDTU-Buero",
    "dtu_serial": "199980126212",
    "dtu_password": "dtu-password-secret",
    "latitude": "52.5",
    "longitude": "13.4",
}


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Home Assistant's MQTT integration leaves a periodic timer behind."""
    return True


async def test_the_diagnostics_hold_the_plan_and_no_identifying_value(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    mqtt_mock: MagicMock,
    freezer,
) -> None:
    await _clock(hass, freezer, "2026-10-05T02:05")
    tibber.today = _items(date(2026, 10, 4), {})
    tibber.tomorrow = _items(date(2026, 10, 5), TWO_CHEAP)
    gx = SimGx(host=SECRETS["host"])
    gx.portal_id = SECRETS["portal"]
    gx.charge(level=100.0, power=0.0)
    _grid(hass, 100.0)
    entry = await setup_entry(
        hass,
        dtu_network,
        _dtu(),
        houses=[
            SimHouse(
                "Home",
                inverters=[OMA, BUERO4],
                tibber_home=HOME_1,
                gx=gx,
                capacity=10.0,
                grid_topic=SECRETS["topic"],
                unique_id="house-unique-secret",
                stored_switches={
                    "curtailment": "off",
                    "publish_grid_power": "on",
                    "grid_charging": "on",
                },
            )
        ],
        tibber=tibber,
        options={CONF_NOTIFY_TARGET: SECRETS["notify"]},
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diagnostics)

    for name, secret in SECRETS.items():
        assert secret not in text, name
    for secret in (OMA, BUERO4, HOME_1, TIBBER_TOKEN, "house-unique-secret"):
        assert secret not in text, secret

    # The inverters are told apart by stable placeholders.
    assert "inverter-1" in text
    assert "inverter-2" in text
    assert "dtu-1" in text

    house = diagnostics["houses"][0]
    charging = house["grid_charging"]
    assert house["name"] == "Home"
    assert house["battery"]["charge_level"] == 100.0
    assert charging["settings"]["enabled"] is True
    assert charging["plan"]["held"]
    assert charging["reason"]["code"] == "holding"
    assert charging["reason"]["text"].startswith("Holding the battery")
    quarter = charging["inputs"][0]
    assert set(quarter) == {
        "start",
        "price",
        "qualifies",
        "load_w",
        "pv_w",
        "support_w",
    }
    assert len(charging["inputs"]) > 80

    dtu = diagnostics["dtus"][0]
    assert dtu["last_update_success"] is True
    assert dtu["restart_count"] == 0
    assert {i["serial"] for i in dtu["inverters"]} >= {"inverter-1", "inverter-2"}
    assert all(
        {"name", "reachable", "limit", "power"} <= set(i) for i in dtu["inverters"]
    )

    data = diagnostics["entry"]["subentries"][0]["data"]
    assert data["password"] != SECRETS["dtu_password"]
    assert diagnostics["entry"]["options"]["tibber_token"] != TIBBER_TOKEN
