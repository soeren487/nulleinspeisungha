# 12: Hand the true Grid Power to the AC Battery

**What to build:** The integration publishes the House's Grid Power to the AC Battery's virtual grid meter on every meter update, so that it can take over from Node-RED. Publishing is controlled by a per-House switch, off by default, so that the owner decides when the hand-over happens. When the Grid Meter fails, publishing stops and the battery goes idle.

**Blocked by:** 08 (Read the AC Battery and give it priority), 11 (Test Victron override and frozen-value behaviour)

**Status:** ready-for-agent

- [ ] With the switch on, every Grid Meter update is published in the format the virtual grid meter expects, with the sign it expects
- [ ] With the switch off nothing is published
- [ ] On Grid Meter failure publishing stops
- [ ] The published value is always the measured value
- [ ] With the owner present: after publishing is stopped, and after the same value is repeated without change, what the AC Battery does is observed on the real system and recorded in the wiki
- [ ] At start, and whenever Grid Charging is not active, any setpoint or discharge override on the GX that the integration may have left behind is released
