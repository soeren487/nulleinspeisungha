# 12: Hand the true Grid Power to the AC Battery

**What to build:** The integration publishes the House's Grid Power to the AC Battery's virtual grid meter on every meter update, so that it can take over from Node-RED. Publishing is controlled by a per-House switch, off by default, so that the owner decides when the hand-over happens. When the Grid Meter fails, publishing stops and the battery goes idle.

**Blocked by:** 08 (Read the AC Battery and give it priority), 11 (Test Victron override and frozen-value behaviour)

**Status:** resolved

- [x] With the switch on, every Grid Meter update is published in the format the virtual grid meter expects, with the sign it expects
- [x] With the switch off nothing is published
- [x] On Grid Meter failure publishing stops
- [x] The published value is always the measured value
- [ ] With the owner present: after publishing is stopped, and after the same value is repeated without change, what the AC Battery does is observed on the real system and recorded in the wiki
- [x] At start, and whenever Grid Charging is not active, any setpoint or discharge override on the GX that the integration may have left behind is released

## Comments

2026-10-06: Built on branch `ticket-12-reported-grid-power`; 615 tests pass twice, linters clean. Publishing goes through Home Assistant's MQTT integration to a topic stored per House, behind a switch that is off by default; every Grid Meter report is published, and there is deliberately no repeat timer, so the driver's 60 s timeout is the dead-man. While the switch is on the House also detects another publisher on its topic and releases overrides left on the GX. Beyond the ticket: a topic containing a wildcard is refused in the form. Remaining, with the owner: the hand-over from his current publisher on the real system, and observing what the AC Battery does when publishing stops and when the same value repeats.

2026-10-06: Closed on the owner's word: the integration publishes the grid power on both Houses, the AC Batteries regulate on it, and his previous publisher is switched off. The one criterion left unticked was not done: what the AC Battery does when publishing stops, and when the same value keeps repeating, has not been observed on the real system. The driver's documented 60 s timeout is what the design relies on.
