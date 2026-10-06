"""Grid Charging of one House: plan the cheap quarter-hours and carry the plan out.

The decision is ``charging_planner``. This object keeps the owner's settings,
replans when something that matters changed, and tells the GX through the wanted
setpoint override (``battery_overrides`` writes and releases it) to charge from
the grid while a planned quarter-hour is on.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_utc_time_change,
)
from homeassistant.util import dt as dt_util

from . import solar, sunrise_energy
from .charging_planner import (
    ChargingPlan,
    PlanReason,
    discharge_block_active,
    plan_charging,
)
from .price_source import PriceLevel

if TYPE_CHECKING:
    from .house import House

DEFAULT_CHARGE_TARGET = 100.0
DEFAULT_BATTERY_EFFICIENCY = 78.0
"""Percent, as a round trip."""
DEFAULT_FORECAST_SHARE = 70.0
"""Percent of the PV Forecast that is counted."""

PRICE_LEVEL_VERY_CHEAP = "very_cheap"
PRICE_LEVEL_CHEAP_AND_BELOW = "cheap_and_below"
PRICE_LEVEL_NORMAL_AND_BELOW = "normal_and_below"
PRICE_LEVEL_OPTIONS: dict[str, frozenset[PriceLevel]] = {
    PRICE_LEVEL_VERY_CHEAP: frozenset({PriceLevel.VERY_CHEAP}),
    PRICE_LEVEL_CHEAP_AND_BELOW: frozenset({PriceLevel.VERY_CHEAP, PriceLevel.CHEAP}),
    PRICE_LEVEL_NORMAL_AND_BELOW: frozenset(
        {PriceLevel.VERY_CHEAP, PriceLevel.CHEAP, PriceLevel.NORMAL}
    ),
}
"""The choices of the owner and the Price Levels that qualify for each."""
DEFAULT_PRICE_LEVELS = PRICE_LEVEL_CHEAP_AND_BELOW

REPLAN_LEVEL_STEP = 1.0
"""Percentage points the charge level may move before the plan is made again."""
SETPOINT_TOLERANCE = 50.0
"""W the wanted setpoint must move by before it is changed."""
SETPOINT_INTERVAL = 10.0
"""Seconds between two recomputations of the wanted setpoint while charging."""


class GridChargingState(StrEnum):
    """What Grid Charging is doing, for the owner."""

    OFF = "off"
    NOT_IN_CONTROL = "not_in_control"
    BATTERY_UNAVAILABLE = "battery_unavailable"
    DYNAMIC_ESS_ACTIVE = "dynamic_ess_active"
    NO_PRICES = "no_prices"
    TARGET_REACHED = "target_reached"
    NO_QUALIFYING_SLOT = "no_qualifying_slot"
    BLOCKED_BY_EFFICIENCY = "blocked_by_efficiency"
    WAITING = "waiting"
    CHARGING = "charging"


_STATE_OF_REASON = {
    PlanReason.NO_PRICES: GridChargingState.NO_PRICES,
    PlanReason.TARGET_REACHED: GridChargingState.TARGET_REACHED,
    PlanReason.NO_QUALIFYING_SLOT: GridChargingState.NO_QUALIFYING_SLOT,
    PlanReason.BLOCKED_BY_EFFICIENCY: GridChargingState.BLOCKED_BY_EFFICIENCY,
}


class GridCharging:
    """The settings, the Charging Plan and the execution for one House."""

    def __init__(self, house: House) -> None:
        """Create it switched off, with the default settings."""
        self._house = house
        self.enabled = False
        """Whether Grid Charging is switched on."""
        self.ignore_efficiency = False
        self.charge_target = DEFAULT_CHARGE_TARGET
        self.battery_efficiency = DEFAULT_BATTERY_EFFICIENCY
        self.price_levels = DEFAULT_PRICE_LEVELS
        self.use_forecast = True
        """Whether the PV Forecast may limit the energy to buy, once it is usable."""
        self.forecast_share = DEFAULT_FORECAST_SHARE
        self.forecast_in_use = False
        """Whether the current plan was computed with the forecast bound."""
        self.energy_needed: float | None = None
        """kWh the battery must hold at sunrise; ``None`` when it cannot be told."""
        self.battery_at_sunrise: float | None = None
        """kWh expected in the battery at sunrise without any charging."""
        self.forecast_surplus: float | None = None
        """kWh of counted forecast above the Expected Load in the 24 h after sunrise."""
        self.plan: ChargingPlan | None = None
        """The current Charging Plan; ``None`` until one could be made."""
        self.deadline: datetime | None = None
        """The sunrise the plan was made for."""
        self._planned_level: float | None = None
        self._planned_power: float | None = None
        self._last_wanted: int | None = None
        self._started = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    # -- observers ---------------------------------------------------------

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` when the plan or the state may have changed."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- settings ----------------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        """Switch Grid Charging on or off; off gives the battery back at once."""
        self.enabled = on
        self._settings_changed()

    def set_ignore_efficiency(self, ignore: bool) -> None:
        """Let the efficiency test be skipped, or not."""
        self.ignore_efficiency = ignore
        self._settings_changed()

    def set_charge_target(self, percent: float) -> None:
        """Change the Charge Target in percent."""
        self.charge_target = percent
        self._settings_changed()

    def set_battery_efficiency(self, percent: float) -> None:
        """Change the Battery Efficiency in percent."""
        self.battery_efficiency = percent
        self._settings_changed()

    def set_price_levels(self, option: str) -> None:
        """Choose which Price Levels qualify."""
        self.price_levels = option
        self._settings_changed()

    def set_use_forecast(self, use: bool) -> None:
        """Let the PV Forecast limit the energy to buy, or not."""
        self.use_forecast = use
        self._settings_changed()

    def set_forecast_share(self, percent: float) -> None:
        """Change the counted share of the PV Forecast in percent."""
        self.forecast_share = percent
        self._settings_changed()

    def _settings_changed(self) -> None:
        if self._started:
            self._replan()
        else:
            self._notify()

    # -- life cycle --------------------------------------------------------

    def start(self) -> None:
        """Begin once the restored settings are in place."""
        house = self._house
        assert house.gateway is not None
        assert house.prices is not None
        self._started = True
        self._unsubs = [
            house.gateway.async_add_listener(self._on_battery),
            house.prices.async_add_listener(self._on_prices),
            house.control.async_add_listener(self._on_control),
            async_track_utc_time_change(
                house.hass, self._on_quarter, minute=(0, 15, 30, 45), second=0
            ),
            async_track_time_interval(
                house.hass, self._on_interval, timedelta(seconds=SETPOINT_INTERVAL)
            ),
        ]
        if house.grid_publisher is not None:
            self._unsubs.append(
                house.grid_publisher.async_add_listener(self._on_publisher)
            )
        self._unsubs.append(house.expected_load.async_add_listener(self._on_inputs))
        if house.forecast is not None:
            self._unsubs.append(house.forecast.async_add_listener(self._on_inputs))
        self._replan()

    def stop(self) -> None:
        """Stop following and give the setpoint override back, if it was set."""
        self._started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        self._last_wanted = None
        overrides = self._house.wanted_overrides
        if overrides.setpoint is not None or overrides.max_discharge_power is not None:
            overrides.setpoint = None
            overrides.max_discharge_power = None
            self._house.override_control.evaluate()

    # -- triggers ----------------------------------------------------------

    @callback
    def _on_prices(self) -> None:
        self._replan()

    @callback
    def _on_inputs(self) -> None:
        """The PV Forecast or the Expected Load changed."""
        self._replan()

    @callback
    def _on_quarter(self, _now: datetime) -> None:
        self._replan()

    @callback
    def _on_control(self) -> None:
        # The control notifies on every run; only the Maximum Charge Power counts.
        if self._planned_power != self._house.control.maximum_charge_power:
            self._replan()

    @callback
    def _on_battery(self) -> None:
        battery = self._house.battery
        level = battery.charge_level if battery is not None else None
        plan = self.plan
        if (
            level is not None
            and (
                self._planned_level is None
                or abs(level - self._planned_level) >= REPLAN_LEVEL_STEP
                or (
                    plan is not None
                    and plan.reason is not PlanReason.TARGET_REACHED
                    and level >= self.charge_target
                )
            )
        ) or (level is None and self.plan is not None):
            self._replan()
        else:
            self._evaluate()
            self._notify()

    @callback
    def _on_publisher(self) -> None:
        self._evaluate()
        self._notify()

    @callback
    def _on_interval(self, _now: datetime) -> None:
        self._evaluate(refresh=True)

    # -- planning ----------------------------------------------------------

    def _replan(self) -> None:
        self._make_plan(dt_util.utcnow())
        self._evaluate()
        self._notify()

    def _make_plan(self, now: datetime) -> None:
        house = self._house
        battery = house.battery
        prices = house.prices
        capacity = house.config.battery_capacity
        level = battery.charge_level if battery is not None else None
        self.deadline = solar.next_sunrise(
            house.config.latitude, house.config.longitude, now
        )
        to_store = self._compute_forecast(now, self.deadline, capacity, level)
        self.forecast_in_use = False
        if prices is None or capacity is None or level is None:
            self.plan = None
            self._planned_level = None
            return
        control = house.control
        self._planned_level = level
        self._planned_power = control.maximum_charge_power
        forecast = house.forecast
        bound = (
            to_store
            if self.use_forecast and forecast is not None and forecast.usable
            else None
        )
        self.forecast_in_use = bound is not None
        self.plan = plan_charging(
            now=now,
            prices=prices.prices,
            qualifying=PRICE_LEVEL_OPTIONS[self.price_levels],
            charge_level=level,
            capacity_kwh=capacity,
            charge_target=self.charge_target,
            max_charge_power=control.maximum_charge_power,
            deadline=self.deadline,
            efficiency=self.battery_efficiency / 100.0,
            ignore_efficiency=self.ignore_efficiency,
            max_energy=bound,
        )

    def _compute_forecast(
        self,
        now: datetime,
        deadline: datetime,
        capacity: float | None,
        level: float | None,
    ) -> float | None:
        """Work out the figures of the PV Forecast; return the energy to store.

        They are computed whenever their inputs exist, whether or not the
        forecast is to be used. Production of Battery-backed Inverters at night is
        not counted, so the result errs on the side of buying a little more.
        """
        house = self._house
        self.energy_needed = self.battery_at_sunrise = self.forecast_surplus = None
        forecast = house.forecast
        if forecast is None:
            return None
        slots = forecast.slots
        if not slots:
            return None
        pv = {slot.start: slot.watts for slot in slots}
        load = dict(house.expected_load.upcoming())
        share = self.forecast_share / 100.0
        efficiency = self.battery_efficiency / 100.0
        prices = house.prices
        qualifying = PRICE_LEVEL_OPTIONS[self.price_levels]
        end = sunrise_energy.period_end(
            deadline, prices.prices if prices is not None else (), qualifying
        )
        self.energy_needed = sunrise_energy.energy_needed_at_sunrise(
            deadline, load, pv, share, end, efficiency
        )
        self.forecast_surplus = sunrise_energy.forecast_surplus(
            deadline, load, pv, share
        )
        if capacity is None or level is None:
            return None
        blocks: set[datetime] = set()
        if prices is not None and self._block_applies():
            blocks = {
                slot
                for slot, _ in sunrise_energy.slots(now, deadline)
                if discharge_block_active(max(slot, now), prices.prices, qualifying)
            }
        self.battery_at_sunrise = sunrise_energy.battery_at_sunrise(
            now, deadline, capacity, level, load, blocks, efficiency
        )
        if self.energy_needed is None or self.battery_at_sunrise is None:
            return None
        return sunrise_energy.energy_to_store(
            self.energy_needed, self.battery_at_sunrise
        )

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> GridChargingState:
        """What Grid Charging is doing right now."""
        return self._state_at(dt_util.utcnow())

    def _state_at(self, now: datetime) -> GridChargingState:
        house = self._house
        if not self.enabled:
            return GridChargingState.OFF
        publisher = house.grid_publisher
        if publisher is None or not publisher.enabled:
            return GridChargingState.NOT_IN_CONTROL
        battery = house.battery
        if battery is None or not battery.fresh or battery.charge_level is None:
            return GridChargingState.BATTERY_UNAVAILABLE
        if battery.dynamic_ess_mode:
            return GridChargingState.DYNAMIC_ESS_ACTIVE
        plan = self.plan
        if plan is None:
            return GridChargingState.NO_PRICES
        if plan.reason is PlanReason.CHARGING_PLANNED:
            if plan.is_due(now):
                return GridChargingState.CHARGING
            return GridChargingState.WAITING
        return _STATE_OF_REASON[plan.reason]

    @property
    def discharge_block(self) -> bool:
        """Whether the wanted discharge override is 0 W: a Discharge Block is on."""
        return self._house.wanted_overrides.max_discharge_power == 0.0

    def _block_wanted(self, now: datetime) -> bool:
        """Whether a Discharge Block is wanted: the rule, and we own a fresh battery."""
        if not self._block_applies():
            return False
        prices = self._house.prices
        assert prices is not None
        return discharge_block_active(
            now, prices.prices, PRICE_LEVEL_OPTIONS[self.price_levels]
        )

    def _block_applies(self) -> bool:
        """Whether Grid Charging would apply a Discharge Block at all."""
        house = self._house
        publisher = house.grid_publisher
        battery = house.battery
        prices = house.prices
        return not (
            not self.enabled
            or publisher is None
            or not publisher.enabled
            or battery is None
            or not battery.fresh
            or battery.dynamic_ess_mode
            or prices is None
        )

    @property
    def blocked_by_efficiency(self) -> bool:
        """Whether the plan is empty only because of the Battery Efficiency."""
        return (
            self.plan is not None
            and self.plan.reason is PlanReason.BLOCKED_BY_EFFICIENCY
        )

    def next_start(self) -> datetime | None:
        """Start of the slot being charged in, or else of the next planned one."""
        if self.plan is None:
            return None
        now = dt_util.utcnow()
        return next((s.start for s in self.plan.slots if s.end > now), None)

    # -- execution ---------------------------------------------------------

    def _evaluate(self, refresh: bool = False) -> None:
        """Set the wanted overrides, or none, and have them carried out.

        The discharge override is 0 W during a Discharge Block (``_block_wanted``)
        and nothing otherwise; it is re-evaluated with every trigger here.

        Whatever ends charging acts at once. While charging goes on, the value
        is only recomputed on the timer (``refresh``) and when charging starts:
        the meter, the battery and the Inverters report at their own moments, so
        a recomputation at every report would follow transients.
        """
        if not self._started:
            return
        overrides = self._house.wanted_overrides
        wanted: float | None = None
        if self._state_at(dt_util.utcnow()) is GridChargingState.CHARGING:
            if refresh or overrides.setpoint is None:
                wanted = self._charging_setpoint()
            else:
                wanted = overrides.setpoint
        else:
            self._last_wanted = None
        discharge = 0.0 if self._block_wanted(dt_util.utcnow()) else None
        changed = (
            overrides.setpoint != wanted or overrides.max_discharge_power != discharge
        )
        block_changed = overrides.max_discharge_power != discharge
        if changed:
            overrides.setpoint = wanted
            overrides.max_discharge_power = discharge
            self._house.override_control.evaluate()
        if block_changed:
            self._notify()

    def _charging_setpoint(self) -> float:
        """The grid import in W that makes the battery take the Maximum Charge Power.

        The House's consumption minus what the Inverters produce is what the
        House takes from the grid without the battery; the Maximum Charge Power
        comes on top. A small change is not followed.
        """
        house = self._house
        power = house.control.maximum_charge_power
        consumption = house.consumption()
        if consumption is None:
            value = power
        else:
            value = consumption - (house.inverter_production() or 0.0) + power
        target = round(value)
        last = self._last_wanted
        if last is not None and abs(target - last) < SETPOINT_TOLERANCE:
            target = last
        self._last_wanted = target
        return float(target)
