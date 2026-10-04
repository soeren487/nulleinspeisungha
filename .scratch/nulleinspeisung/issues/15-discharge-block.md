# 15: Block discharge while prices are low

**What to build:** While Grid Charging is switched on, the AC Battery does not discharge in any quarter-hour whose Price Level qualifies, whether or not charging is planned in it. PV surplus still charges it. Outside those quarter-hours, and when Grid Charging is switched off or prices are unavailable, discharge is allowed again.

**Blocked by:** 14 (Charge the AC Battery in the cheapest quarter-hours)

**Status:** ready-for-agent

- [ ] A Discharge Block starts and ends on quarter-hour boundaries following the Price Level
- [ ] The battery's discharge setting is written only when it changes and is restored to its previous value afterwards
- [ ] The House shows whether a Discharge Block is active
- [ ] Switching Grid Charging off, or losing prices, lifts the block immediately
