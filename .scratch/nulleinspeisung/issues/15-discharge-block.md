# 15: Block discharge while prices are low

**What to build:** While Grid Charging is switched on, the AC Battery does not discharge in any quarter-hour whose Price Level qualifies, whether or not charging is planned in it. PV surplus still charges it. Outside those quarter-hours, and when Grid Charging is switched off or prices are unavailable, discharge is allowed again.

**Blocked by:** 14 (Charge the AC Battery in the cheapest quarter-hours)

**Status:** resolved

- [x] A Discharge Block starts and ends on quarter-hour boundaries following the Price Level
- [x] The battery's discharge setting is written only when it changes and is restored to its previous value afterwards
- [x] The House shows whether a Discharge Block is active
- [x] Switching Grid Charging off, or losing prices, lifts the block immediately

## Comments

2026-10-06: Implemented on branch `ticket-15-discharge-block`. 764 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The block uses the volatile discharge override (0 W, released with -1), so the stored discharge limit is never changed. It is active in every quarter-hour whose Price Level qualifies while Grid Charging is on and the integration owns the battery. Known from the real GX: about 47 W of discharge remain. Not yet observed on a real system: that solar surplus still charges the battery during a block.
