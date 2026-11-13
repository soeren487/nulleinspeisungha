"""The reason Grid Charging gives, tested directly: no Home Assistant.

Plans are written out by hand. Times are UTC in the plans and Berlin (UTC+2 on
24 October 2026) in the sentences.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.nulleinspeisung.charging_planner import (
    ChargingPlan,
    HeldFor,
    PlannedSlot,
    PlanReason,
)
from custom_components.nulleinspeisung.charging_reason import (
    ChargingReason,
    ReasonCode,
    explain,
)

BERLIN = ZoneInfo("Europe/Berlin")
QUARTER = timedelta(minutes=15)
NOW = datetime(2026, 10, 24, 0, 5, tzinfo=UTC)  # 02:05 local
MAX_POWER = 2100.0


def utc(hhmm: str, day: int = 24) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime(2026, 10, day, int(hours), int(minutes), tzinfo=UTC)


def slot(hhmm: str, grid: float = 0.525, top_up: float = 0.0) -> PlannedSlot:
    start = utc(hhmm)
    return PlannedSlot(start, start + QUARTER, 0.1, grid, grid * 0.88, top_up)


def make_plan(**kw) -> ChargingPlan:
    base = {
        "slots": (),
        "energy_to_buy": 0.0,
        "top_up_kwh": 0.0,
        "buffer_kwh": 0.0,
        "shortfall": 0.0,
        "uncovered": 0.0,
        "reference_price": None,
        "energy_missing": 0.0,
        "battery_at_sunrise": None,
        "reason": PlanReason.NO_SHORTFALL,
    }
    return ChargingPlan(**(base | kw))


def held(first: str, count: int) -> tuple[datetime, ...]:
    return tuple(utc(first) + QUARTER * i for i in range(count))


def reason(plan: ChargingPlan | None, **kw) -> ChargingReason:
    settings = {
        "enabled": True,
        "in_control": True,
        "battery_available": True,
        "dynamic_ess": False,
        "now": NOW,
        "tz": BERLIN,
        "max_charge_power": MAX_POWER,
        "horizon_end": utc("21:45"),  # 23:45 local
    } | kw
    return explain(plan, **settings)


# -- the states before the plan counts ---------------------------------------


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"enabled": False}, ReasonCode.OFF),
        ({"in_control": False}, ReasonCode.NOT_IN_CONTROL),
        ({"battery_available": False}, ReasonCode.BATTERY_UNAVAILABLE),
        ({"dynamic_ess": True}, ReasonCode.DYNAMIC_ESS_ACTIVE),
    ],
)
def test_the_state_comes_before_the_plan(kw, code) -> None:
    plan = make_plan(slots=(slot("00:00"),), reason=PlanReason.CHARGING_PLANNED)
    result = reason(plan, **kw)

    assert result.code is code
    assert result.text
    assert result.text_de
    assert result.power_w is None


def test_off_wins_over_everything() -> None:
    result = reason(
        None,
        enabled=False,
        in_control=False,
        battery_available=False,
        dynamic_ess=True,
    )

    assert result.code is ReasonCode.OFF
    assert result.text == "Grid charging is switched off."
    assert result.text_de == "Netzladen ist aus."


def test_not_in_control_comes_before_an_unavailable_battery() -> None:
    result = reason(None, in_control=False, battery_available=False)

    assert result.code is ReasonCode.NOT_IN_CONTROL


def test_no_plan_means_no_prices() -> None:
    result = reason(None)

    assert result.code is ReasonCode.NO_PRICES
    assert result.text == "No prices are known for the coming quarter-hours."


def test_a_plan_without_quarters_means_no_prices() -> None:
    result = reason(make_plan(reason=PlanReason.NO_PRICES))

    assert result.code is ReasonCode.NO_PRICES


# -- charging ----------------------------------------------------------------


def test_charging_for_a_shortfall_names_it() -> None:
    plan = make_plan(
        slots=(slot("00:00"), slot("00:15")),
        reason=PlanReason.CHARGING_PLANNED,
        shortfall=1.4,
        reference_price=0.312,
    )
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_PLAN
    assert result.power_w == MAX_POWER
    assert result.next_start == utc("00:00")
    assert result.shortfall_kwh == 1.4
    assert result.shortfall_price == 0.312
    assert result.text == (
        "Charging at 2.1 kW in a cheap quarter-hour to cover a shortfall of "
        "1.4 kWh at 31.2 ct."
    )
    assert result.text_de == (
        "Lädt mit 2,1 kW in einer günstigen Viertelstunde, um einen Engpass von "
        "1,4 kWh zu 31,2 ct zu decken."
    )


def test_charging_without_a_shortfall_is_a_short_sentence() -> None:
    plan = make_plan(slots=(slot("00:00"),), reason=PlanReason.CHARGING_PLANNED)
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_PLAN
    assert result.text == "Charging at 2.1 kW in a cheap quarter-hour."


def test_charging_for_the_top_up() -> None:
    plan = make_plan(
        slots=(slot("00:00", top_up=0.525),),
        reason=PlanReason.CHARGING_PLANNED,
        top_up_kwh=0.525,
    )
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_TOP_UP
    assert result.text == "Charging at 2.1 kW for the BatteryLife top-up."
    assert result.text_de == "Lädt mit 2,1 kW für die BatteryLife-Vollladung."


def test_the_safety_buffer_charges_at_its_own_power_for_the_top_up() -> None:
    plan = make_plan(
        slots=(slot("00:00", grid=0.2, top_up=0.2),),
        reason=PlanReason.CHARGING_PLANNED,
        top_up_kwh=0.2,
        buffer_kwh=0.2,
        buffer_power=1200.0,
    )
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_SAFETY_BUFFER
    assert result.power_w == 1200.0
    assert result.text == (
        "Charging at 1.2 kW for the BatteryLife top-up because the Safety Buffer "
        "applies."
    )
    assert result.text_de == (
        "Lädt mit 1,2 kW für die BatteryLife-Vollladung, weil der "
        "Sicherheitspuffer greift."
    )


def test_the_safety_buffer_for_the_shortfalls() -> None:
    plan = make_plan(
        slots=(slot("00:00", grid=0.2),),
        reason=PlanReason.CHARGING_PLANNED,
        buffer_kwh=0.2,
        buffer_power=1200.0,
    )
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_SAFETY_BUFFER
    assert result.text == (
        "Charging at 1.2 kW for the expensive hours ahead because the Safety "
        "Buffer applies."
    )


def test_a_slot_that_the_normal_plan_charges_in_too_uses_the_full_power() -> None:
    plan = make_plan(
        slots=(slot("00:00", grid=0.525),),
        reason=PlanReason.CHARGING_PLANNED,
        buffer_kwh=0.2,
        buffer_power=1200.0,
    )
    result = reason(plan)

    assert result.code is ReasonCode.CHARGING_SAFETY_BUFFER
    assert result.power_w == MAX_POWER


def test_the_buffer_power_is_limited_by_the_maximum() -> None:
    plan = make_plan(
        slots=(slot("00:00", grid=0.2),),
        reason=PlanReason.CHARGING_PLANNED,
        buffer_kwh=0.2,
        buffer_power=3000.0,
    )

    assert reason(plan).power_w == MAX_POWER


# -- holding -----------------------------------------------------------------


def test_holding_names_the_shortfall_and_the_end_of_the_hold() -> None:
    plan = make_plan(
        held=held("00:00", 16),  # to 04:00 UTC = 06:00 local
        held_for=HeldFor(utc("17:00"), 1.4, 0.312),
        slots=(slot("02:00"),),
        reason=PlanReason.CHARGING_PLANNED,
    )
    result = reason(plan)

    assert result.code is ReasonCode.HOLDING
    assert result.held_until == utc("04:00")
    assert result.shortfall_kwh == 1.4
    assert result.shortfall_at == utc("17:00")
    assert result.shortfall_price == 0.312
    assert result.next_start == utc("02:00")
    assert result.text == (
        "Holding the battery until 06:00 for a shortfall of 1.4 kWh at 19:00 (31.2 ct)."
    )
    assert result.text_de == (
        "Hält die Batterie bis 06:00 für einen Engpass von 1,4 kWh um 19:00 (31,2 ct)."
    )


def test_a_hold_ends_where_the_run_of_held_quarters_ends() -> None:
    plan = make_plan(held=held("00:00", 2) + held("01:00", 4))

    assert reason(plan).held_until == utc("00:30")


def test_a_hold_without_a_named_shortfall_has_a_general_sentence() -> None:
    plan = make_plan(held=held("00:00", 4))
    result = reason(plan)

    assert result.code is ReasonCode.HOLDING
    assert result.shortfall_kwh is None
    assert result.text == (
        "Holding the battery until 03:00 to keep the stored energy for later."
    )


def test_holding_names_the_day_when_the_hold_goes_past_midnight() -> None:
    plan = make_plan(
        held=held("21:30", 1) + held("21:45", 8),
        held_for=HeldFor(utc("10:00", day=25), 0.5, 0.4),
    )
    result = reason(plan, now=utc("21:35"))

    assert result.text == (
        "Holding the battery until tomorrow 01:45 for a shortfall of 0.5 kWh at "
        "tomorrow 11:00 (40.0 ct)."  # the clocks go back at 03:00
    )
    assert "morgen 01:45" in result.text_de


def test_a_planned_quarter_in_a_hold_is_charging_not_holding() -> None:
    plan = make_plan(
        held=held("00:00", 4),
        slots=(slot("00:00"),),
        reason=PlanReason.CHARGING_PLANNED,
    )

    assert reason(plan).code is ReasonCode.CHARGING_PLAN


# -- waiting and nothing to do -----------------------------------------------


def test_waiting_names_the_next_start() -> None:
    plan = make_plan(slots=(slot("00:30"),), reason=PlanReason.CHARGING_PLANNED)
    result = reason(plan)

    assert result.code is ReasonCode.WAITING
    assert result.next_start == utc("00:30")
    assert result.text == "Waiting; next charging at 02:30."
    assert result.text_de == "Wartet; nächstes Laden um 02:30."


def test_waiting_skips_slots_that_are_over() -> None:
    plan = make_plan(
        slots=(slot("00:00"), slot("00:30")), reason=PlanReason.CHARGING_PLANNED
    )

    assert reason(plan, now=utc("00:20")).next_start == utc("00:30")


@pytest.mark.parametrize(
    ("plan_reason", "text"),
    [
        (
            PlanReason.NO_SHORTFALL,
            "Nothing to do: no shortfall until the known prices end at 23:45.",
        ),
        (PlanReason.AT_TARGET, "Nothing to do: the battery is at its Charge Target."),
        (
            PlanReason.NO_QUALIFYING_SLOT,
            "Nothing to do: no qualifying quarter-hour before the shortfall.",
        ),
        (
            PlanReason.BLOCKED_BY_EFFICIENCY,
            "Nothing to do: charging is not worth it at the Battery Efficiency.",
        ),
    ],
)
def test_nothing_to_do_says_why(plan_reason, text) -> None:
    result = reason(make_plan(reason=plan_reason))

    assert result.code is ReasonCode.IDLE
    assert result.text == text
    assert result.text_de.startswith("Nichts zu tun")
    assert result.power_w is None
    assert result.held_until is None


def test_no_shortfall_without_a_known_end_of_the_prices() -> None:
    result = reason(make_plan(), horizon_end=None)

    assert result.text == "Nothing to do: no shortfall in the known prices."


def test_every_code_has_a_case_above() -> None:
    seen = {
        reason(None, enabled=False).code,
        reason(None, in_control=False).code,
        reason(None, battery_available=False).code,
        reason(None, dynamic_ess=True).code,
        reason(None).code,
        reason(make_plan(slots=(slot("00:00"),))).code,
        reason(make_plan(slots=(slot("00:00", top_up=0.5),))).code,
        reason(make_plan(slots=(slot("00:00"),), buffer_kwh=0.2)).code,
        reason(make_plan(held=held("00:00", 2))).code,
        reason(
            make_plan(slots=(slot("00:30"),), reason=PlanReason.CHARGING_PLANNED)
        ).code,
        reason(make_plan()).code,
    }

    assert seen == set(ReasonCode)
