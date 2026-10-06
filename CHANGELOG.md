# Changelog

## Unreleased

### New

- **Grid charging buys only what the sun will not deliver.** Once the PV forecast is usable, "Charge from grid" no longer fills the battery to the Charge target by sunrise by default, but buys only what the battery must hold at sunrise to carry the House until the sun covers its consumption, or until the next cheap quarter-hour, and never more than the Charge target allows. The energy needed is the largest deficit that builds up from sunrise on: the expected consumption minus the counted share of the forecast, added up quarter-hour by quarter-hour. What the battery will still hold at sunrise without any charging (its Discharge Blocks included) is subtracted. The switch "Use PV forecast" ("PV-Prognose berücksichtigen", on by default) turns this off. The number "Counted share of PV forecast" ("Angerechneter Anteil der PV-Prognose", default 70 %, 10 to 100 %) says how much of the forecast is trusted. The sensors "Energy needed at sunrise", "Battery at sunrise" and "Forecast surplus" show the figures, also while the feature is off or not yet usable, and the binary sensor "PV forecast in use" says whether the current plan was made with it.
- It takes effect only once the forecast is usable (14 days of production history). Until the consumption has been learned (a week of history), the fallback daily consumption stands in for it. Before that, grid charging fills to the Charge target as before.
- The production of Battery-backed Inverters at night is not counted, so the energy bought errs on the side of a little too much.

## 0.4.0

### New

- **Charging the AC battery from the grid.** A House with an AC battery, Tibber prices and a usable capacity can charge its battery in the cheapest quarter-hours before the next sunrise, across midnight. It has its own device, "<House> Netzladen" (English "<House> Grid charging"), below the House device. Switch "Aus dem Netz laden" ("Charge from grid") turns it on; it is off by default, because it spends money. Choose the Charge target (default 100 %) and the price level at which charging is allowed: only very cheap, cheap and very cheap (default), or normal and cheaper. The plan is made again at every quarter-hour, when new prices arrive, when you change a setting and when the charge level moves by a percentage point. During a planned quarter-hour the battery charges at the maximum charge power; afterwards it is released. Switching it off, losing the prices, losing the battery or switching off "Send grid power to Victron" releases the battery at once. Nothing is charged while Dynamic ESS is active on the GX. Sensors show the state, the start of the next charging, the energy to buy, the energy missing and the reference price; the sensor "Next charging start" lists all planned quarter-hours in its attribute "slots".
- **Efficiency check.** A battery gives back only part of what is charged into it: the setting "AC battery efficiency" (default 78 %, as a round trip) says how much. A quarter-hour is only used for charging if its price is at most this share of the reference price, which is the mean price of the quarter-hours after sunrise that are not cheap enough to charge in, over the following 24 hours. If cheap quarter-hours exist but none passes the check, nothing is charged and the binary sensor "Grid charging blocked by efficiency" is on. Switch "Ignore efficiency check" skips the check; it is off by default. The efficiency also decides how much energy is bought: for every kWh that should end up in the battery, a little more is taken from the grid.
- **Discharge block.** While "Charge from grid" is on, the AC battery is not allowed to discharge in any quarter-hour whose price level is allowed for charging (very cheap, cheap or normal, as you chose), whether or not charging is planned in it. The house then draws its power from the grid instead of from the battery, so the battery keeps its energy for the expensive hours. The block starts and ends exactly at the quarter-hour boundaries, and is lifted at once when you switch "Charge from grid" off, when the prices are lost, when the battery stops answering, when Dynamic ESS turns on or when you switch off "Send grid power to Victron". The binary sensor "Discharge block active" ("Entladesperre aktiv") on the grid charging device shows it. About 50 W of discharge remain during a block (measured on a real system). The block only limits discharging, so solar surplus should still charge the battery; that has not been observed on a real system yet.

### Good to know

- **"Charge from grid" is off after the update.** The grid charging device already shows the plan it would follow, so you can check a night's plan before switching it on.
- **Grid charging and the discharge block only act on a House where "Send grid power to Victron" is on.** Otherwise the state reads "not in control" and nothing is written to the GX.
- **Before tomorrow's prices are published** (around 13:00) there is no reference price, and the efficiency check is skipped for that plan.
- Dynamic ESS must be off on the GX.
- Restart Home Assistant after updating.

## 0.3.1

### Changed

- The two House switches have clearer names. "Curtailment" is now "Regulate feed-in" ("Einspeisung regeln"), and "Publish grid power" is now "Send grid power to Victron" ("Netzleistung an Victron senden"). Entity ids and unique ids stay the same.
- Both switches are on by default for a newly created House. A House that already exists keeps the state its switches have, on or off.
- The inverter control of a House has its own device, "<House> Wechselrichter-Regelung" (English "<House> Inverter control"), below the House device. It holds the switch "Regulate feed-in", the numbers Feed-in Setpoint, Update Interval, tolerance band, limit floor, limit slew rate and response reserve, the select "On failure", and the sensors control state, Inverter Limit and Battery-backed Inverter Limit. Everything else stays on the House device. For an existing House the entities move by themselves; entity ids, states and history are kept.

### Fixed

- A House whose AC battery was idle at exactly 0 W raised the repair issue "AC battery does not answer", although the GX was connected. The GX sends a value only when it changes, so an idle battery sent nothing. The integration now judges the GX by its heartbeat.

### Good to know

- **A newly created House starts regulating its inverters at once**, because "Regulate feed-in" is now on by default. Switch it off right after creating the House if you want to set the parameters first.
- **"Send grid power to Victron" starts publishing as soon as a grid meter topic is entered.** If another system still publishes to that topic, the integration raises a repair issue until that system is switched off.
- Restart Home Assistant after updating.

## 0.3.0

### New

- **Reported grid power.** A House with an AC battery can publish its grid power to the topic the Victron's grid meter driver reads, taking over from an existing publisher. Enter the topic in the House's battery step and switch "publish grid power" on when you hand over; it is off by default. While it is on, the integration warns if another system still publishes to the same topic, and releases setpoint and discharge overrides left on the GX. If the grid meter stops reporting or Home Assistant stops, publishing stops as well, and the driver's own timeout takes the meter away from the battery.

### Fixed

- Adding a DTU whose DTU serial is already used by a different DTU was refused with "this DTU was already added", although it never was. The form now says that another DTU uses the same serial and that each DTU needs its own serial in OpenDTU under Settings > DTU Settings.

### Good to know

- Publishing needs Home Assistant's MQTT integration, connected to the broker the grid meter driver reads from.
- While "publish grid power" is off, the integration writes nothing to the GX.
- Grid charging and the discharge block are not included yet. If your current publisher also does grid charging, that stops when you switch it off.
- Restart Home Assistant after updating.

## 0.2.0

First version with the control features. Everything that sets inverter limits is off until you switch it on.

### New

- **Curtailment.** Per House, a switch that regulates the inverters so that grid power stays at the feed-in setpoint. Settings: feed-in setpoint, update interval, tolerance band, limit floor, behaviour on failure, limit slew rate and response reserve.
- **Two inverter groups.** Battery-backed inverters are lowered first and raised last, and are held to what the house consumes.
- **AC battery.** A House can be given a Victron GX by its address. The integration reads charge level, battery power and settings, and Curtailment leaves room for what the battery can still take. Nothing is written to the GX.
- **Tibber prices.** One token in the integration's options, a Tibber home per House, and sensors for the current price and price level.
- **PV forecast.** Per House, from Open-Meteo irradiance and the House's own production history. Usable after 14 days of data.
- **Expected load.** Per House, learned from its own consumption per quarter-hour. Learned after 7 days; until then a fallback daily consumption is used.
- **Failure handling.** When the grid meter delivers no data or no DTU of a House answers, the House holds its limits or, if chosen, sets its inverters to 100 %, and raises a repair issue.
- **Stuck DTU detection uses the Houses.** Battery-backed inverters are ignored, and a DTU is also restarted when another DTU of the same House is producing while it delivers no data.

### Changed

- The House form has a further step for the AC battery. Existing Houses keep working; open a House's settings once to add a battery or a Tibber home.

### Good to know

- **Inverters with firmware from 2020 follow a limit slowly**, at about 0.5 % of rated power per second. From 100 % it can take minutes before their output falls. A response reserve of about 10 % keeps their limit close above the output so that Curtailment takes effect in seconds. Inverters with later firmware respond within seconds and need no reserve.
- The limit slew rate is one setting per House. A House that mixes slow and fast inverters is not handled well yet.
- Curtailment has been tested against simulated devices built from measurements, not yet in continuous operation on a real installation. Start with one House and watch its control state.
- Grid charging and the discharge block are not included yet.
- Requires Home Assistant 2026.9 or later. Restart Home Assistant after updating.

## 0.1.0

- The integration can be added, DTUs connected, and their inverters shown.
