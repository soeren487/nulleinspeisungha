"""End-to-end tests of Tibber prices in a House, with a simulated Tibber API."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.nulleinspeisung.const import (
    CONF_NOTIFY_TARGET,
    CONF_TIBBER_TOKEN,
    DOMAIN,
)
from tests.conftest import (
    HOME_1,
    HOME_2,
    TIBBER_TOKEN,
    DtuNetwork,
    PollDelay,
    SimHouse,
    SimTibber,
    recorded_price_info,
    setup_entry,
)

HOUSE = "house-home"
RECORDED = recorded_price_info()


def recorded(day: str, hhmm: str) -> dict[str, Any]:
    """The recorded price item of a day ('today'/'tomorrow') at a local time."""
    return next(i for i in RECORDED[day] if f"T{hhmm}:00" in i["startsAt"])


async def freeze_at(hass: HomeAssistant, freezer, local: str) -> None:
    """Freeze at a Berlin wall-clock time on 2026-10-04 (UTC+2)."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(datetime.fromisoformat(f"2026-10-04T{local}:00+02:00"))


async def advance(hass: HomeAssistant, freezer, minutes: int, step: int = 1) -> None:
    """Let ``minutes`` pass, ``step`` minutes at a time."""
    for _ in range(minutes // step):
        freezer.tick(timedelta(minutes=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def advance_to(hass: HomeAssistant, freezer, local: str) -> None:
    """Let time pass minute by minute until a Berlin time on 2026-10-04 (or later)."""
    target = datetime.fromisoformat(f"2026-10-04T{local}:00+02:00")
    while datetime.now(target.tzinfo) < target:
        await advance(hass, freezer, 1)


def entity_id(hass: HomeAssistant, key: str, house: str = HOUSE) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{house}_{key}")


def state(hass: HomeAssistant, key: str, house: str = HOUSE) -> str:
    eid = entity_id(hass, key, house)
    assert eid is not None, key
    return hass.states.get(eid).state


def issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def setup(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    house: SimHouse | None = None,
    **kwargs: Any,
):
    return await setup_entry(
        hass,
        dtu_network,
        houses=[house or SimHouse("Home", tibber_home=HOME_1)],
        tibber=tibber,
        **kwargs,
    )


async def test_price_and_level_match_the_recorded_data(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """At 10:07 the House shows the 10:00 slot of the recorded prices."""
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)

    item = recorded("today", "10:00")
    assert float(state(hass, "price")) == item["total"] == 0.3284
    assert state(hass, "price_level") == "normal"
    attributes = hass.states.get(entity_id(hass, "price")).attributes
    assert attributes["unit_of_measurement"] == "EUR/kWh"
    assert attributes["state_class"] == "measurement"
    assert state(hass, "prices_known_until") == "2026-10-05T22:00:00+00:00"
    level = hass.states.get(entity_id(hass, "price_level"))
    assert list(level.attributes["options"]) == [
        "very_cheap",
        "cheap",
        "normal",
        "expensive",
        "very_expensive",
    ]
    known = er.async_get(hass).async_get(entity_id(hass, "prices_known_until"))
    assert known.entity_category is er.EntityCategory.DIAGNOSTIC
    assert len(tibber.price_requests) == 1


async def test_request_uses_the_configured_token_and_home(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """The one setup request carries the token and names the House's home."""
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber, SimHouse("Home", tibber_home=HOME_2))
    [(query, headers)] = tibber.requests
    assert f'home(id: "{HOME_2}")' in query
    assert headers["Authorization"] == f"Bearer {TIBBER_TOKEN}"
    assert headers["User-Agent"].startswith("nulleinspeisung/")


async def test_price_changes_at_the_next_quarter_hour_without_a_request(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """At 10:15 the price and level change; no new request is made."""
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)
    assert state(hass, "price_level") == "normal"

    await advance(hass, freezer, 7)
    assert float(state(hass, "price")) == 0.3284  # still 10:14
    await advance(hass, freezer, 1)

    item = recorded("today", "10:15")
    assert float(state(hass, "price")) == item["total"] == 0.3123
    assert state(hass, "price_level") == "cheap"
    assert len(tibber.requests) == 1


async def test_no_request_in_a_quiet_period(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """With tomorrow already known, six hours pass with no request at all."""
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)
    assert state(hass, "prices_known_until") == "2026-10-05T22:00:00+00:00"

    await advance_to(hass, freezer, "16:07")

    assert len(tibber.requests) == 1
    assert float(state(hass, "price")) == recorded("today", "16:00")["total"]
    assert state(hass, "price_level") == "cheap"


async def test_polling_for_tomorrow_starts_at_13_and_stops_when_found(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """From 13:00 every 15 minutes until tomorrow's prices are there."""
    tibber.published = False
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)
    assert len(tibber.price_requests) == 1
    assert state(hass, "prices_known_until") == "2026-10-04T22:00:00+00:00"

    await advance_to(hass, freezer, "12:59")
    assert len(tibber.price_requests) == 1
    await advance_to(hass, freezer, "13:00")
    assert len(tibber.price_requests) == 2
    await advance_to(hass, freezer, "13:14")
    assert len(tibber.price_requests) == 2
    await advance_to(hass, freezer, "13:15")
    assert len(tibber.price_requests) == 3
    await advance_to(hass, freezer, "13:29")
    assert len(tibber.price_requests) == 3

    tibber.published = True
    await advance_to(hass, freezer, "13:30")
    assert len(tibber.price_requests) == 4
    assert state(hass, "prices_known_until") == "2026-10-05T22:00:00+00:00"

    await advance_to(hass, freezer, "18:00")
    assert len(tibber.price_requests) == 4


async def test_each_poll_gets_its_own_random_delay(
    hass: HomeAssistant,
    dtu_network: DtuNetwork,
    tibber: SimTibber,
    freezer,
    poll_delay: PollDelay,
) -> None:
    """The delay is drawn once per poll and added to the wait."""
    poll_delay.queue.extend([200, 100, 0])  # setup, first poll, second poll
    tibber.published = False
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)

    await advance_to(hass, freezer, "13:03")  # 13:00 + 200 s is 13:03:20
    assert len(tibber.price_requests) == 1
    await advance_to(hass, freezer, "13:04")
    assert len(tibber.price_requests) == 2
    # Polled at 13:04:00; next at 13:19:00 + 100 s = 13:20:40.
    await advance_to(hass, freezer, "13:20")
    assert len(tibber.price_requests) == 2
    await advance_to(hass, freezer, "13:21")
    assert len(tibber.price_requests) == 3
    # The third delay is 0: next at 13:21 + 15 min.
    await advance_to(hass, freezer, "13:35")
    assert len(tibber.price_requests) == 3
    await advance_to(hass, freezer, "13:36")
    assert len(tibber.price_requests) == 4


async def test_a_failed_poll_keeps_the_old_prices(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Tibber failing at 13:00 changes nothing the owner sees; polling goes on."""
    tibber.published = False
    await freeze_at(hass, freezer, "12:50")
    await setup(hass, dtu_network, tibber)
    tibber.down = True

    await advance_to(hass, freezer, "13:16")
    assert len(tibber.price_requests) == 3  # setup, 13:00, 13:15
    assert float(state(hass, "price")) == recorded("today", "13:15")["total"]
    assert state(hass, "prices_known_until") == "2026-10-04T22:00:00+00:00"
    assert issue(hass, f"prices_unavailable_{HOUSE}") is None

    tibber.down = False
    tibber.published = True
    await advance_to(hass, freezer, "13:31")
    assert state(hass, "prices_known_until") == "2026-10-05T22:00:00+00:00"


async def test_prices_running_out_raise_an_issue_after_15_minutes(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """After midnight with no new prices: unknown at once, issue after 15 minutes."""
    tibber.published = False
    calls = async_mock_service(hass, "notify", "send_message")
    await freeze_at(hass, freezer, "23:40")
    await setup(hass, dtu_network, tibber, options={CONF_NOTIFY_TARGET: "notify.me"})
    assert float(state(hass, "price")) == recorded("today", "23:30")["total"]

    await advance_to(hass, freezer, "23:59")
    assert float(state(hass, "price")) == recorded("today", "23:45")["total"]
    before = len(tibber.price_requests)

    await advance(hass, freezer, 1)  # 00:00
    assert state(hass, "price") == "unknown"
    assert state(hass, "price_level") == "unknown"
    assert state(hass, "prices_known_until") == "2026-10-04T22:00:00+00:00"
    assert len(tibber.price_requests) == before + 1  # looked for new prices at once

    await advance(hass, freezer, 14)  # 00:14
    assert issue(hass, f"prices_unavailable_{HOUSE}") is None
    assert calls == []
    # Retries come every five minutes: 00:05 and 00:10.
    assert len(tibber.price_requests) == before + 3

    await advance(hass, freezer, 2)  # 00:16
    raised = issue(hass, f"prices_unavailable_{HOUSE}")
    assert raised is not None
    assert raised.translation_key == "prices_unavailable"
    assert raised.translation_placeholders == {"house": "Home"}
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.me"
    assert "Home" in calls[0].data["message"]

    # Tibber now answers with the next day as today.
    tibber.today, tibber.tomorrow = tibber.tomorrow, []
    await advance(hass, freezer, 5)  # 00:21
    assert issue(hass, f"prices_unavailable_{HOUSE}") is None
    assert float(state(hass, "price")) == recorded("tomorrow", "00:15")["total"]
    assert len(calls) == 1


async def test_rejected_token_raises_its_own_issue_and_clears(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """A rejected token gives a repair issue and a message; fixing it clears it."""
    calls = async_mock_service(hass, "notify", "send_message")
    tibber.token = "changed-at-tibber"
    await freeze_at(hass, freezer, "10:07")
    entry = await setup(
        hass,
        dtu_network,
        tibber,
        options={CONF_NOTIFY_TARGET: "notify.me", CONF_TIBBER_TOKEN: TIBBER_TOKEN},
    )
    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "price") == "unknown"

    raised = issue(hass, "tibber_token_rejected")
    assert raised is not None
    assert raised.translation_key == "tibber_token_rejected"
    assert len(calls) == 1
    assert "token" in calls[0].data["message"].lower()

    await advance(hass, freezer, 5)
    assert issue(hass, "tibber_token_rejected") is not None
    assert len(calls) == 1  # told once

    tibber.token = TIBBER_TOKEN
    await advance(hass, freezer, 5)
    assert issue(hass, "tibber_token_rejected") is None
    assert float(state(hass, "price")) == recorded("today", "10:15")["total"]


async def test_rejected_token_in_a_poll_keeps_the_prices(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Prices already known stay in use while the token issue is open."""
    tibber.published = False
    await freeze_at(hass, freezer, "12:50")
    await setup(hass, dtu_network, tibber)
    tibber.token = "changed-at-tibber"

    await advance_to(hass, freezer, "13:01")
    assert issue(hass, "tibber_token_rejected") is not None
    assert issue(hass, f"prices_unavailable_{HOUSE}") is None
    assert float(state(hass, "price")) == recorded("today", "13:00")["total"]


async def test_entry_loads_and_recovers_when_tibber_is_down_at_setup(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Tibber down at setup: entry loads, price unknown, retried every 5 minutes."""
    tibber.down = True
    await freeze_at(hass, freezer, "10:07")
    entry = await setup(hass, dtu_network, tibber)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "price") == "unknown"
    assert state(hass, "price_level") == "unknown"
    assert state(hass, "prices_known_until") == "unknown"
    assert len(tibber.price_requests) == 1

    await advance(hass, freezer, 4)
    assert len(tibber.price_requests) == 1
    await advance(hass, freezer, 1)
    assert len(tibber.price_requests) == 2

    tibber.down = False
    await advance(hass, freezer, 5)
    assert float(state(hass, "price")) == recorded("today", "10:15")["total"]
    assert len(tibber.price_requests) == 3
    await advance(hass, freezer, 30)
    assert len(tibber.price_requests) == 3


async def test_unavailable_for_longer_than_15_minutes_from_setup_raises_issue(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Tibber down from the start: the issue appears once 15 minutes have passed."""
    tibber.down = True
    await freeze_at(hass, freezer, "10:07")
    await setup(hass, dtu_network, tibber)
    await advance(hass, freezer, 14)
    assert issue(hass, f"prices_unavailable_{HOUSE}") is None
    await advance(hass, freezer, 2)
    assert issue(hass, f"prices_unavailable_{HOUSE}") is not None


async def test_house_without_home_has_no_price_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """A House without a Tibber home shows no prices and asks Tibber nothing."""
    await freeze_at(hass, freezer, "10:07")
    await setup_entry(
        hass,
        dtu_network,
        houses=[
            SimHouse("Plain"),
            SimHouse("Priced", tibber_home=HOME_1),
        ],
        tibber=tibber,
    )
    for key in ("price", "price_level", "prices_known_until"):
        assert entity_id(hass, key, "house-plain") is None
        assert entity_id(hass, key, "house-priced") is not None
    assert len(tibber.requests) == 1


async def test_home_without_token_has_no_price_entities(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Without a token no entity appears and Tibber is not asked."""
    await freeze_at(hass, freezer, "10:07")
    await setup_entry(hass, dtu_network, houses=[SimHouse("Home", tibber_home=HOME_1)])
    assert entity_id(hass, "price") is None
    assert tibber.requests == []


async def test_two_houses_use_their_own_homes(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Each House asks for its own home and shows its own prices."""
    await freeze_at(hass, freezer, "10:07")
    await setup_entry(
        hass,
        dtu_network,
        houses=[
            SimHouse("A", tibber_home=HOME_1),
            SimHouse("B", tibber_home=HOME_2),
        ],
        tibber=tibber,
    )
    queries = tibber.price_requests
    assert len(queries) == 2
    assert any(HOME_1 in q for q in queries)
    assert any(HOME_2 in q for q in queries)
    assert state(hass, "price", "house-a") == state(hass, "price", "house-b")


async def test_entering_a_token_makes_the_prices_appear(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """Saving a token in the options reloads the entry and the House shows prices."""
    await freeze_at(hass, freezer, "10:07")
    entry = await setup_entry(
        hass, dtu_network, houses=[SimHouse("Home", tibber_home=HOME_1)]
    )
    assert entity_id(hass, "price") is None
    tibber.register()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIBBER_TOKEN: TIBBER_TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert float(state(hass, "price")) == 0.3284


async def test_house_offers_prices_to_later_features(
    hass: HomeAssistant, dtu_network: DtuNetwork, tibber: SimTibber, freezer
) -> None:
    """``house.prices`` holds the current price and all future ones."""
    await freeze_at(hass, freezer, "10:07")
    entry = await setup(hass, dtu_network, tibber)
    house = next(iter(entry.runtime_data.houses.values()))

    assert house.prices.current.total == 0.3284
    assert house.prices.current.level.value == "normal"
    future = house.prices.future
    assert future[0].start == datetime.fromisoformat("2026-10-04T10:15:00+02:00")
    assert len(future) == 192 - 41
    assert future[-1].start == datetime.fromisoformat("2026-10-05T23:45:00+02:00")
    assert house.prices.known_until == datetime.fromisoformat(
        "2026-10-06T00:00:00+02:00"
    )


async def test_house_without_prices_has_none(
    hass: HomeAssistant, dtu_network: DtuNetwork
) -> None:
    """A House without Tibber home has no ``prices``."""
    entry = await setup_entry(hass, dtu_network, houses=[SimHouse("Plain")])
    assert next(iter(entry.runtime_data.houses.values())).prices is None
