"""Tests of entering the Tibber token and choosing a Tibber home per House."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nulleinspeisung.const import (
    CONF_NOTIFY_TARGET,
    CONF_TIBBER_HOME,
    CONF_TIBBER_TOKEN,
    DOMAIN,
    SUBENTRY_TYPE_HOUSE,
)
from tests.conftest import (
    HOME_1,
    HOME_2,
    TIBBER_TOKEN,
    DtuNetwork,
    SimDtu,
    SimHouse,
    SimTibber,
    setup_entry,
)

# -- options flow ----------------------------------------------------------


async def _options(
    hass: HomeAssistant, entry: ConfigEntry, user_input: dict[str, Any]
) -> ConfigFlowResult:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )


async def test_valid_token_is_stored_after_trying_it(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """The owner enters a token; it is tried against Tibber and kept."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await _options(hass, entry, {CONF_TIBBER_TOKEN: f"  {TIBBER_TOKEN} "})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_TIBBER_TOKEN] == TIBBER_TOKEN
    assert len(tibber.home_requests) == 1
    assert tibber.requests[0][1]["Authorization"] == f"Bearer {TIBBER_TOKEN}"


async def test_invalid_token_is_reported_in_the_form(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """A rejected token shows an error on the token field and is not stored."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    result = await _options(hass, entry, {CONF_TIBBER_TOKEN: "wrong"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TIBBER_TOKEN: "invalid_token"}
    assert CONF_TIBBER_TOKEN not in entry.options

    # Correcting it in the same form works.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIBBER_TOKEN: TIBBER_TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_TIBBER_TOKEN] == TIBBER_TOKEN


async def test_connection_problem_is_reported_in_the_form(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """When Tibber cannot be reached the form says so and keeps the old token."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={CONF_TIBBER_TOKEN: "old"})
    entry.add_to_hass(hass)
    tibber.down = True

    result = await _options(hass, entry, {CONF_TIBBER_TOKEN: TIBBER_TOKEN})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.options[CONF_TIBBER_TOKEN] == "old"


async def test_clearing_the_token_removes_it_without_asking_tibber(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """An empty token field removes the token and keeps the notification target."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_TIBBER_TOKEN: TIBBER_TOKEN, CONF_NOTIFY_TARGET: "notify.owner"},
    )
    entry.add_to_hass(hass)

    result = await _options(
        hass, entry, {CONF_NOTIFY_TARGET: "notify.owner", CONF_TIBBER_TOKEN: ""}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_TIBBER_TOKEN not in entry.options
    assert entry.options[CONF_NOTIFY_TARGET] == "notify.owner"
    assert tibber.requests == []


async def test_options_form_offers_the_stored_token(
    hass: HomeAssistant, tibber: SimTibber
) -> None:
    """Opening the options shows the token already entered, so saving keeps it."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_TIBBER_TOKEN: TIBBER_TOKEN}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {
        key.schema: key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested[CONF_TIBBER_TOKEN] == TIBBER_TOKEN


# -- House flow ------------------------------------------------------------


def _first(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "location": {"latitude": 50.0, "longitude": 8.0, "radius": 100},
        "grid_meter": "sensor.grid_meter",
        "grid_meter_sign": "import",
        **extra,
    }


async def _start(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry | None = None
) -> ConfigFlowResult:
    if subentry is None:
        return await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_HOUSE), context={"source": "user"}
        )
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_HOUSE),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )


async def _next(
    hass: HomeAssistant, result: ConfigFlowResult, data: dict[str, Any]
) -> ConfigFlowResult:
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


def _home_field(result: ConfigFlowResult):
    """The Tibber home select of the first step, or ``None`` when not shown."""
    for key, value in result["data_schema"].schema.items():
        if key == CONF_TIBBER_HOME:
            assert isinstance(value, selector.SelectSelector)
            return key, value
    return None


def _house(entry: ConfigEntry, name: str) -> ConfigSubentry:
    return next(
        s
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_TYPE_HOUSE and s.title == name
    )


async def _finish(hass: HomeAssistant, result: ConfigFlowResult) -> ConfigFlowResult:
    """Pass the Inverter step of a House without Inverters."""
    assert result["step_id"] == "inverters"
    result = await _next(hass, result, {"inverters": []})
    await hass.async_block_till_done()
    return result


async def test_home_select_is_not_shown_without_a_token(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """Without a token the owner is not asked for a Tibber home."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default())
    result = await _start(hass, entry)
    assert _home_field(result) is None
    assert tibber.requests == []


async def test_home_select_lists_the_homes_by_nickname(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """With a token the select offers 'none' and the account's homes."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default(), tibber=tibber)
    result = await _start(hass, entry)

    key, select = _home_field(result)
    options = [(o["value"], o["label"]) for o in select.config["options"]]
    assert options == [("none", "None"), (HOME_1, "Home 1"), (HOME_2, "Home 2")]
    assert key.default() == "none"


async def test_chosen_home_is_stored_as_its_id(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """Choosing a home stores its id in the House."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default(), tibber=tibber)
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Home", tibber_home=HOME_2))
    result = await _finish(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _house(entry, "Home").data[CONF_TIBBER_HOME] == HOME_2


async def test_choosing_none_stores_no_home(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """The choice 'none' leaves the House without a Tibber home."""
    entry = await setup_entry(hass, dtu_network, SimDtu.default(), tibber=tibber)
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Home", tibber_home="none"))
    await _finish(hass, result)
    assert CONF_TIBBER_HOME not in _house(entry, "Home").data


async def test_home_of_another_house_is_refused(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """Two Houses cannot use the same Tibber home."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("First", tibber_home=HOME_1)],
        tibber=tibber,
    )
    result = await _start(hass, entry)
    result = await _next(hass, result, _first("Second", tibber_home=HOME_1))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_TIBBER_HOME: "tibber_home_assigned"}

    result = await _next(hass, result, _first("Second", tibber_home=HOME_2))
    result = await _finish(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_shows_the_stored_home_and_allows_keeping_it(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """A House's own home is the default and is not 'used by another House'."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("First", tibber_home=HOME_1)],
        tibber=tibber,
    )
    house = _house(entry, "First")
    result = await _start(hass, entry, house)
    key, _ = _home_field(result)
    assert key.default() == HOME_1

    result = await _next(hass, result, _first("First", tibber_home=HOME_1))
    assert result["step_id"] == "inverters"
    result = await _finish(hass, result)
    assert _house(entry, "First").data[CONF_TIBBER_HOME] == HOME_1


async def test_reconfigure_can_remove_the_home(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """Choosing 'none' while reconfiguring takes the home away."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("First", tibber_home=HOME_1)],
        tibber=tibber,
    )
    result = await _start(hass, entry, _house(entry, "First"))
    result = await _next(hass, result, _first("First", tibber_home="none"))
    await _finish(hass, result)
    assert CONF_TIBBER_HOME not in _house(entry, "First").data


async def test_reconfigure_keeps_the_home_when_homes_cannot_be_fetched(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """With Tibber unreachable the select is missing and the home stays."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("First", tibber_home=HOME_1)],
        tibber=tibber,
    )
    tibber.down = True
    result = await _start(hass, entry, _house(entry, "First"))
    assert _home_field(result) is None

    result = await _next(hass, result, _first("First renamed"))
    result = await _finish(hass, result)
    await hass.async_block_till_done()
    assert _house(entry, "First renamed").data[CONF_TIBBER_HOME] == HOME_1


async def test_rejected_token_hides_the_select(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber
) -> None:
    """A token that Tibber now refuses is treated like an unreachable Tibber."""
    entry = await setup_entry(
        hass,
        dtu_network,
        SimDtu.default(),
        houses=[SimHouse("First", tibber_home=HOME_1)],
        tibber=tibber,
    )
    tibber.token = "changed-at-tibber"
    result = await _start(hass, entry, _house(entry, "First"))
    assert _home_field(result) is None
    result = await _next(hass, result, _first("First"))
    await _finish(hass, result)
    assert _house(entry, "First").data[CONF_TIBBER_HOME] == HOME_1
