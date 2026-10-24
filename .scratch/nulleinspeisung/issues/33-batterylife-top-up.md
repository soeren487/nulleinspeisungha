# 33: Charge to 95 % once a day so that BatteryLife lowers its minimum

**What to build:** With ESS mode "Optimized (with BatteryLife)" the Victron raises the minimum charge level it holds by 5 % on every day without a good charge, up to 80 %, and lowers it by 5 % only on a day the battery reaches 95 % (Victron ESS manual, "Controlling depth of discharge"; see the wiki page on Victron ESS control). Shortfall planning (ticket 32) buys only what expensive periods need, so the battery seldom reaches 95 % in autumn and winter, the minimum climbs and the usable capacity shrinks. On the owner's installation the active minimum stood at 75 % on 2026-10-24 with 20 % configured.

The House therefore adds a BatteryLife Top-up to its Charging Plan: while the Victron's active minimum is above its configured minimum and the battery has not reached 95 % today, Grid Charging charges up to 95 % in the cheapest qualifying quarter-hours of the rest of the day. Charging happens only in quarter-hours that qualify ("when it is cheap"), as the owner asked on 2026-10-24.

**Blocked by:** None (can start immediately)

**Status:** resolved

Decisions taken in planning:

- The trigger is observable on the GX: the active minimum (`system/0/Control/ActiveSocLimit`) is above the configured minimum (`.../BatteryLife/MinimumSocLimit`). With BatteryLife off both are equal, so the top-up never runs.
- "Today" is the local calendar day. Victron does not document when its day ends; local midnight is the assumption.
- The top-up is planned before the shortfalls, so the shortfall planning counts the energy it stores and does not buy twice.
- The counted PV Forecast is part of the simulation: if the battery is expected to reach 95 % from PV alone, nothing is bought.
- No efficiency test: what is bought is usable capacity, not energy for a later price. The Charge Target and the Maximum Charge Power still apply, so a Charge Target below 95 % rules the top-up out.
- A switch per House, on by default, can turn the top-up off. It acts only while Grid Charging is on.
- Whether 95 % was reached today is remembered in memory only; after a restart of Home Assistant on a day that already reached 95 % the top-up may be bought once more.

- [x] While the active minimum is above the configured minimum and the battery has not reached 95 % today, the plan charges in the cheapest qualifying quarter-hours of the rest of the day until the battery is expected to reach 95 %
- [x] Nothing is bought for the top-up when the battery reached 95 % today, when the counted PV Forecast reaches it, when no qualifying quarter-hour is left today, or when the active minimum equals the configured minimum
- [x] The Charge Target and the Maximum Charge Power are respected; the efficiency test does not apply to the top-up
- [x] Shortfall planning sees the energy the top-up stores
- [x] A switch per House, on by default, turns the top-up off
- [x] The House shows the energy planned for the top-up
- [x] The rule is recorded in the spec and the glossary

## Comments

2026-10-24: Implemented on branch `ticket-33-batterylife-top-up`; 923 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The planner fills to 95 % first, in the cheapest qualifying quarter-hours before local midnight, then covers the shortfalls on top. New switch `batterylife_top_up` (on by default) and sensor `top_up_energy` on the Grid Charging device. Not yet run on the real system.
