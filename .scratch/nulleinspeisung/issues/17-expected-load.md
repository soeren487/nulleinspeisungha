# 17: Learn the Expected Load

**What to build:** Each House records its consumption per quarter-hour, computed from Grid Power, Inverter production and AC Battery power, and from that provides the Expected Load for each coming quarter-hour as a weighted average of the same quarter-hour on past days. Until a week of history exists a configured daily consumption is used.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** resolved

- [x] Consumption per quarter-hour is recorded and survives a Home Assistant restart
- [x] The Expected Load for the next 24 hours is available and shown
- [x] Recent days weigh more than older ones
- [x] With less than a week of history the configured value is used, spread evenly
- [x] The calculation is tested directly

## Comments

2026-10-05: Implemented on branch `ticket-17-expected-load`. 511 tests pass, linters clean. Consumption is sampled every 30 s and stored per quarter-hour for 35 days. The Expected Load of a slot is the weighted mean of the same local quarter-hour on past days, weights halving every 7 days. It counts as learned after 7 days with at least 72 recorded quarter-hours each; until then the fallback daily consumption (a number entity, default 10 kWh) is spread evenly. Sampling and storage stay separate from the PV Forecast's, whose records carry different data.
