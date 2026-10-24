# 32: Plan Grid Charging against shortfalls in expensive periods

**What to build:** Grid Charging currently asks one question: how much must the AC Battery hold at sunrise to carry the House until the next cheap quarter-hour. When a cheap period follows soon after sunrise, the answer is close to zero, and an expensive period before the deadline (an evening) is never prepared for: the battery runs to its minimum and the House buys at the expensive price, although it could have charged in cheap quarter-hours before. Seen on the owner's installation on 2026-10-24: 0.06 kWh "needed at sunrise", nothing bought, battery at its minimum.

The House instead walks through every coming quarter-hour up to the end of the known prices, follows the battery's content, finds the energy it would have to buy in quarter-hours that do not qualify for charging, and buys that energy beforehand in the cheapest qualifying quarter-hours, where the Battery Efficiency says it pays.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] An expensive evening that the battery cannot carry is prepared for by charging in cheap quarter-hours before it, also when a cheap period follows soon after sunrise
- [x] A night that is cheap throughout is not used for charging when the battery is not needed before the next cheap period
- [x] Each quarter-hour is only charged in if its price, after the Battery Efficiency, is below the price of the shortfall it covers
- [x] The Charge Target, the Maximum Charge Power, the Victron's minimum charge level, Discharge Blocks, the PV Forecast and the DC Battery Support are respected
- [x] The House shows the shortfall it found, the part it can cover, and the energy it will buy
- [x] The rule is recorded in the spec and replaces the sunrise-based rule

## Comments

2026-10-24: Implemented on branch `ticket-32-shortfall-planning`; 904 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The planner simulates the AC Battery over every quarter-hour up to the end of the known prices (at most 48 hours), finds shortfalls in quarter-hours that do not qualify, and covers them earliest first in the cheapest qualifying quarter-hours before them that pass the efficiency test against the shortfall's own price; ties go to the later slot, and a running planned quarter-hour is kept until it ends. In the recreated real case it buys 1.43 kWh in three cheap morning quarter-hours for a 1.11 kWh evening shortfall and nothing at night. Without a usable PV Forecast it counts no PV. Not yet run on the real system.
