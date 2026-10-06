# 14: Charge the AC Battery in the cheapest quarter-hours

**What to build:** With Grid Charging switched on, a House computes a Charging Plan: the cheapest qualifying quarter-hours before sunrise that cover the energy missing to the Charge Target at Maximum Charge Power. In those quarter-hours it makes the AC Battery charge from the grid, using the mechanism decided in ticket 11. The plan is shown and kept current.

**Blocked by:** 12 (Hand the true Grid Power to the AC Battery), 13 (Fetch Tibber prices per House)

**Status:** resolved

- [x] The House has a Grid Charging switch, a Charge Target number, and sensors for the Charging Plan and the energy to buy
- [x] The plan picks the cheapest quarter-hours among the qualifying Price Levels up to sunrise, across midnight
- [x] The plan is recomputed when prices arrive, every quarter-hour, and when the charge level deviates from it
- [x] During a planned quarter-hour the battery charges at Maximum Charge Power; outside it the battery is released
- [x] Switching Grid Charging off releases the battery immediately
- [x] Without prices nothing is charged
- [x] The planner is tested directly; execution is tested against a simulated broker
- [x] A Battery Efficiency setting per House (default 78 %) decides whether a quarter-hour is worth charging in, against the Reference Price, and how much energy has to be bought
- [x] When the Battery Efficiency is what prevents charging, the House shows it
- [x] A switch per House, off by default, makes Grid Charging ignore the efficiency test
- [x] Grid Charging only acts on a House where the integration sends the grid power to the Victron; otherwise it shows why it does nothing

## Comments

2026-10-06: Implemented on branch `ticket-14-grid-charging`. 730 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The planner is pure; execution writes the Victron setpoint override, recomputed every 10 s and written on a change of 50 W or more. The owner's additions are in: a Battery Efficiency setting (default 78 %) decides against a Reference Price whether a quarter-hour is worth charging in and how much has to be bought; the House shows when the efficiency blocks charging; a switch makes Grid Charging ignore the test. Grid Charging has its own device per House and is off by default. The plan is computed while it is off, so its effect can be seen first. Not yet run on the real system: the first real charge needs the hand-over of ticket 12 on that House.
