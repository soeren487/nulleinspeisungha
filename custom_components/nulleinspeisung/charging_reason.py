"""Why Grid Charging does what it does now: a code, the figures and a sentence.

A pure function of the Charging Plan and a few facts about the state. No Home
Assistant, no I/O; the time and the time zone are passed in. The sentence comes in
English and in German, built from the same figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum

from .charging_planner import ChargingPlan, PlanReason
from .quarters import SLOT

_EPSILON = 1e-6


class ReasonCode(StrEnum):
    """What Grid Charging does in the current quarter-hour."""

    OFF = "off"
    NOT_IN_CONTROL = "not_in_control"
    BATTERY_UNAVAILABLE = "battery_unavailable"
    DYNAMIC_ESS_ACTIVE = "dynamic_ess_active"
    NO_PRICES = "no_prices"
    CHARGING_PLAN = "charging_plan"
    CHARGING_TOP_UP = "charging_top_up"
    CHARGING_SAFETY_BUFFER = "charging_safety_buffer"
    HOLDING = "holding"
    WAITING = "waiting"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class ChargingReason:
    """The code, the figures it rests on and the sentence."""

    code: ReasonCode
    text: str
    """One English sentence."""
    text_de: str
    """The same sentence in German."""
    power_w: float | None = None
    """The power charged at now."""
    next_start: datetime | None = None
    """Start of the planned quarter-hour being charged in or the next one."""
    shortfall_kwh: float | None = None
    shortfall_at: datetime | None = None
    shortfall_price: float | None = None
    held_until: datetime | None = None
    """End of the run of held quarter-hours that now is in."""


def explain(
    plan: ChargingPlan | None,
    *,
    enabled: bool,
    in_control: bool,
    battery_available: bool,
    dynamic_ess: bool,
    now: datetime,
    tz: tzinfo,
    max_charge_power: float,
    horizon_end: datetime | None = None,
) -> ChargingReason:
    """What Grid Charging does at ``now`` and why.

    ``horizon_end`` is where the quarter-hours given to the planner end (the known
    prices or the Expected Load); ``tz`` is the zone the times are written in.
    """
    t = _Words(now, tz)
    if not enabled:
        return _plain(
            ReasonCode.OFF, "Grid charging is switched off.", "Netzladen ist aus."
        )
    if not in_control:
        return _plain(
            ReasonCode.NOT_IN_CONTROL,
            "Not in control: the Grid Power is not handed to the AC Battery.",
            "Nicht in Regelung: die Netzleistung wird nicht an die Batterie übergeben.",
        )
    if not battery_available:
        return _plain(
            ReasonCode.BATTERY_UNAVAILABLE,
            "The AC Battery does not report.",
            "Die Batterie meldet sich nicht.",
        )
    if dynamic_ess:
        return _plain(
            ReasonCode.DYNAMIC_ESS_ACTIVE,
            "Dynamic ESS is active and decides.",
            "Dynamic ESS ist aktiv und entscheidet.",
        )
    if plan is None or plan.reason is PlanReason.NO_PRICES:
        return _plain(
            ReasonCode.NO_PRICES,
            "No prices are known for the coming quarter-hours.",
            "Keine Preise für die kommenden Viertelstunden bekannt.",
        )

    upcoming = next((s for s in plan.slots if s.end > now), None)
    next_start = upcoming.start if upcoming is not None else None
    due = upcoming if upcoming is not None and upcoming.start <= now else None
    if due is not None:
        power = _charge_power(plan, due.grid_kwh, max_charge_power)
        kw = power / 1000.0
        on_top_up = due.top_up_kwh > _EPSILON
        if plan.buffer_kwh > _EPSILON:
            code = ReasonCode.CHARGING_SAFETY_BUFFER
            what = (
                ("for the BatteryLife top-up", "für die BatteryLife-Vollladung")
                if on_top_up
                else (
                    "for the expensive hours ahead",
                    "für die teuren Stunden danach",
                )
            )
            en = (
                f"Charging at {t.num(kw, 'en')} kW {what[0]} "
                "because the Safety Buffer applies."
            )
            de = (
                f"Lädt mit {t.num(kw, 'de')} kW {what[1]}, "
                "weil der Sicherheitspuffer greift."
            )
        elif on_top_up:
            code = ReasonCode.CHARGING_TOP_UP
            en = f"Charging at {t.num(kw, 'en')} kW for the BatteryLife top-up."
            de = f"Lädt mit {t.num(kw, 'de')} kW für die BatteryLife-Vollladung."
        else:
            code = ReasonCode.CHARGING_PLAN
            en = f"Charging at {t.num(kw, 'en')} kW in a cheap quarter-hour"
            de = f"Lädt mit {t.num(kw, 'de')} kW in einer günstigen Viertelstunde"
            if plan.shortfall > _EPSILON:
                en += (
                    f" to cover a shortfall of {t.num(plan.shortfall, 'en')} kWh"
                    f"{t.price_en(plan.reference_price)}."
                )
                de += (
                    f", um einen Engpass von {t.num(plan.shortfall, 'de')} kWh"
                    f"{t.price_de(plan.reference_price)} zu decken."
                )
            else:
                en += "."
                de += "."
        return ChargingReason(
            code,
            en,
            de,
            power_w=power,
            next_start=next_start,
            shortfall_kwh=plan.shortfall or None,
            shortfall_price=plan.reference_price,
        )

    held_until = _held_until(plan, now)
    if held_until is not None:
        held_for = plan.held_for
        en = f"Holding the battery until {t.when(held_until, 'en')}"
        de = f"Hält die Batterie bis {t.when(held_until, 'de')}"
        if held_for is not None:
            en += (
                f" for a shortfall of {t.num(held_for.kwh, 'en')} kWh at "
                f"{t.when(held_for.start, 'en')}{t.price_en(held_for.price, True)}."
            )
            de += (
                f" für einen Engpass von {t.num(held_for.kwh, 'de')} kWh um "
                f"{t.when(held_for.start, 'de')}{t.price_de(held_for.price, True)}."
            )
        else:
            en += " to keep the stored energy for later."
            de += ", um die gespeicherte Energie für später aufzuheben."
        return ChargingReason(
            ReasonCode.HOLDING,
            en,
            de,
            next_start=next_start,
            shortfall_kwh=held_for.kwh if held_for else None,
            shortfall_at=held_for.start if held_for else None,
            shortfall_price=held_for.price if held_for else None,
            held_until=held_until,
        )

    if plan.reason is PlanReason.CHARGING_PLANNED:
        if next_start is None:
            return _plain(ReasonCode.WAITING, "Waiting.", "Wartet.")
        return ChargingReason(
            ReasonCode.WAITING,
            f"Waiting; next charging at {t.when(next_start, 'en')}.",
            f"Wartet; nächstes Laden um {t.when(next_start, 'de')}.",
            next_start=next_start,
            shortfall_kwh=plan.shortfall or None,
            shortfall_price=plan.reference_price,
        )

    shortfall = plan.shortfall if plan.shortfall > _EPSILON else None
    en, de = _nothing_to_do(plan.reason, t, horizon_end)
    return ChargingReason(
        ReasonCode.IDLE,
        en,
        de,
        shortfall_kwh=shortfall,
        shortfall_price=plan.reference_price,
    )


def _nothing_to_do(
    reason: PlanReason, t: _Words, horizon_end: datetime | None
) -> tuple[str, str]:
    if reason is PlanReason.NO_SHORTFALL:
        if horizon_end is None:
            return (
                "Nothing to do: no shortfall in the known prices.",
                "Nichts zu tun: kein Engpass in den bekannten Preisen.",
            )
        return (
            f"Nothing to do: no shortfall until the known prices end at "
            f"{t.when(horizon_end, 'en')}.",
            f"Nichts zu tun: kein Engpass bis die bekannten Preise um "
            f"{t.when(horizon_end, 'de')} enden.",
        )
    if reason is PlanReason.AT_TARGET:
        return (
            "Nothing to do: the battery is at its Charge Target.",
            "Nichts zu tun: die Batterie hat ihr Ladeziel erreicht.",
        )
    if reason is PlanReason.NO_QUALIFYING_SLOT:
        return (
            "Nothing to do: no qualifying quarter-hour before the shortfall.",
            "Nichts zu tun: keine passende Viertelstunde vor dem Engpass.",
        )
    return (
        "Nothing to do: charging is not worth it at the Battery Efficiency.",
        "Nichts zu tun: Laden lohnt sich bei diesem Wirkungsgrad nicht.",
    )


def _plain(code: ReasonCode, en: str, de: str) -> ChargingReason:
    return ChargingReason(code, en, de)


def _charge_power(plan: ChargingPlan, grid_kwh: float, maximum: float) -> float:
    """The power in W charged at in the due slot: the Safety Buffer's when it alone
    makes the House charge there, else the Maximum Charge Power."""
    if plan.buffer_power is None or grid_kwh - plan.buffer_kwh > _EPSILON:
        return maximum
    return min(plan.buffer_power, maximum)


def _held_until(plan: ChargingPlan, now: datetime) -> datetime | None:
    """The end of the run of held quarter-hours that ``now`` is in."""
    starts = set(plan.held)
    current = next((s for s in starts if s <= now < s + SLOT), None)
    if current is None:
        return None
    end = current + SLOT
    while end in starts:
        end += SLOT
    return end


class _Words:
    """Numbers and times of the sentences in the local time zone."""

    def __init__(self, now: datetime, tz: tzinfo) -> None:
        self._now = now.astimezone(tz)
        self._tz = tz

    @staticmethod
    def num(value: float, lang: str) -> str:
        text = f"{value:.1f}"
        return text.replace(".", ",") if lang == "de" else text

    def when(self, moment: datetime, lang: str) -> str:
        """``06:00``; with the day when it is not today (``tomorrow 06:00``)."""
        local = moment.astimezone(self._tz)
        clock = local.strftime("%H:%M")
        days = (local.date() - self._now.date()).days
        if days == 0:
            return clock
        if days == 1:
            return f"{'morgen' if lang == 'de' else 'tomorrow'} {clock}"
        return f"{local:%d.%m.} {clock}"

    def price_en(self, price: float | None, bare: bool = False) -> str:
        return self._price(price, "en", bare)

    def price_de(self, price: float | None, bare: bool = False) -> str:
        return self._price(price, "de", bare)

    def _price(self, price: float | None, lang: str, bare: bool) -> str:
        if price is None:
            return ""
        cents = f"{self.num(price * 100.0, lang)} ct"
        if bare:
            return f" ({cents})"
        return f" at {cents}" if lang == "en" else f" zu {cents}"
