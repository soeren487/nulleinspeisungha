# 17: Learn the Expected Load

**What to build:** Each House records its consumption per quarter-hour, computed from Grid Power, Inverter production and AC Battery power, and from that provides the Expected Load for each coming quarter-hour as a weighted average of the same quarter-hour on past days. Until a week of history exists a configured daily consumption is used.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** ready-for-agent

- [ ] Consumption per quarter-hour is recorded and survives a Home Assistant restart
- [ ] The Expected Load for the next 24 hours is available and shown
- [ ] Recent days weigh more than older ones
- [ ] With less than a week of history the configured value is used, spread evenly
- [ ] The calculation is tested directly
