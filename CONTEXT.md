# Nulleinspeisung

Control of photovoltaic inverters and AC batteries so that each house exchanges a chosen amount of power with the grid, and charges its battery from the grid when electricity is cheap.

## Language

### Site

**House**:
One grid connection point with exactly one Grid Meter, any number of assigned Inverters, at most one AC Battery and at most one Tibber home.
_Avoid_: Site, home, building, installation

**Grid Meter**:
The measurement of net power across all phases at a House's grid connection. Positive is import, negative is export.
_Avoid_: Smart meter, Shelly, power sensor

**Grid Power**:
The value a Grid Meter reports at one moment.
_Avoid_: Consumption, grid intake

### Photovoltaics

**DTU**:
A gateway that talks to Inverters by radio and is reached over the network. It is connected once, independent of any House.
_Avoid_: OpenDTU device, gateway, bridge

**Inverter**:
A microinverter reached through exactly one DTU and assigned to at most one House.

**Battery-backed Inverter**:
An Inverter whose input is a DC Battery, so power it is not allowed to deliver stays stored.
_Avoid_: Battery inverter, storage inverter

**PV Inverter**:
An Inverter fed directly by panels, so power it is not allowed to deliver is lost.
_Avoid_: Plain inverter, normal inverter

**DC Battery**:
A battery between panels and a Battery-backed Inverter that decides by itself when to release energy.
_Avoid_: Solix, small battery

**Inverter Limit**:
The share of its rated power an Inverter is currently allowed to deliver.
_Avoid_: Power setpoint, throttle, cap

**Stuck DTU**:
A DTU that still answers on the network but no longer exchanges data with its Inverters by radio.
_Avoid_: Hung, frozen, crashed DTU

### Control

**Feed-in Setpoint**:
The Grid Power a House's control aims for, expressed as export: positive means export that much, negative means import that much, zero means neither.
_Avoid_: Feed-in limit, power setpoint, target

**Update Interval**:
The time between two successive adjustments of a House's Inverter Limits.
_Avoid_: Cycle time, poll interval

**Curtailment**:
Lowering Inverter Limits because a House would otherwise export more than its Feed-in Setpoint.
_Avoid_: Throttling, limiting, regulation

### AC battery and tariff

**AC Battery**:
A House's large battery, charged and discharged on the AC side by its own inverter/charger, which regulates on the Grid Power it is told.
_Avoid_: Victron, Multiplus, big battery

**Reported Grid Power**:
The Grid Power value handed to an AC Battery. It equals the measured Grid Power unless Grid Charging is shifting it.
_Avoid_: Virtual meter value, fake meter value

**Maximum Charge Power**:
The highest power at which a House's AC Battery can charge.

**Grid Charging**:
Deliberately charging an AC Battery with imported power because the price is low.
_Avoid_: Forced charging, cheap charging, Tibber charging

**Price Level**:
The tariff provider's rating of a quarter-hour's price relative to surrounding prices: very cheap, cheap, normal, expensive, very expensive.
_Avoid_: Price rating, tariff level

**Charge Target**:
The charge level a House's AC Battery should have reached by sunrise through Grid Charging.
_Avoid_: Target SoC, goal

**Charging Plan**:
The set of upcoming quarter-hours in which Grid Charging will run for a House.
_Avoid_: Schedule, slots

**Discharge Block**:
A period during which an AC Battery must not discharge, because the Price Level is low and the stored energy is worth more later.
_Avoid_: Hold, discharge lock

### Forecast

**PV Forecast**:
The expected production of a House's Inverters for each coming quarter-hour.
_Avoid_: Weather forecast, solar forecast

**Expected Load**:
The power a House is expected to consume in each coming quarter-hour, learned from its own past.
_Avoid_: Consumption forecast, base load
