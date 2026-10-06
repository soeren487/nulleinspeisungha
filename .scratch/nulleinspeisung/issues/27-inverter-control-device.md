# 27: Put the inverter control of a House on its own device

**What to build:** The switch that regulates feed-in and its parameters sit on the House device under Home Assistant's fixed heading "Konfiguration", which does not say what they are for and cannot be renamed by an integration. Each House gets a second device, named after the House and "Wechselrichter-Regelung" / "Inverter control", that holds everything about regulating the Inverters: the switch, its parameters and its state. The House device keeps the measurements, the AC Battery, prices, forecast and expected load.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Each House has a device "<House> Wechselrichter-Regelung" (English "<House> Inverter control") below the House device
- [x] It holds the switch "Einspeisung regeln", the numbers for Feed-in Setpoint, Update Interval, tolerance band, limit floor, limit slew rate and response reserve, the select for the behaviour on failure, and the sensors for control state, Inverter Limit and Battery-backed limit
- [x] Everything else stays on the House device
- [x] For a House that already exists the entities move to the new device and keep their entity ids, states and history
- [x] Removing a House removes both devices

## Comments

2026-10-06: Implemented on branch `ticket-27-inverter-control-device`. 655 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The device name is translated through a device translation key. Home Assistant moves existing entities to the new device by itself; entity ids are pinned so that new Houses get the same ids as before. Found on the way: the list of Inverters offered to a House would have included the new control device; it now accepts only Inverter devices.
