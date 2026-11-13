# 38: BatteryLife Top-up to 100 %

**What to build:** The owner had better results with BatteryLife when the battery was charged to 100 %, not 95 %. After three weeks of top-ups to 95 % the Victron's minimum still moved between 50 and 80 %. The top-up level becomes a setting per House, default 100 %.

**Blocked by:** None

**Status:** resolved

Decisions taken in planning:

- A number "BatteryLife top-up level" ("Ziel der BatteryLife-Vollladung"), 90 to 100 %, step 1, default 100, on the Grid Charging device.
- The top-up counts as reached for the day when the charge level reaches that level. A battery reports 100 % only at the end of absorption, so charging near the end is slow; the planner's simulation does not know that. The top-up therefore stays wanted until the level is really reported.
- The Charge Target still caps it.

- [x] The top-up charges to the set level, default 100 %
- [x] Reached is judged against the set level
- [x] The number is restored after a restart

## Comments

2026-11-13: Implemented: number `top_up_level`, default 100 %.
