# Changelog

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
