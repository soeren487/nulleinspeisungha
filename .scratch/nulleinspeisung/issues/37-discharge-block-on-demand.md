# 37: Block discharge only when the stored energy is needed for a dearer time

**What to build:** The Discharge Block is on in every quarter-hour whose Price Level qualifies. Tibber's Price Level compares with the days before, not with an absolute price. From 2026-11-11 21:30 the level was "cheap" or "very cheap" for more than 39 hours (24.3 ct at noon on 2026-11-13); the living house took about 30 kWh from the grid in that time while its AC Battery stood at 95 to 98 % and never discharged, and 4.5 kWh bought for the top-up on 2026-11-12 stayed unused.

The Charging Plan decides the Discharge Block per quarter-hour: the battery is held in a qualifying quarter-hour only when the plan shows that the energy it would deliver there is needed later in a quarter-hour with a higher price and would be missing there (a shortfall would arise or grow, or more would have to be bought). Otherwise the battery discharges as usual.

**Blocked by:** None

**Status:** resolved

Decisions taken in planning:

- The planner simulates without any Discharge Block first. If no shortfall arises in a dearer quarter-hour after a qualifying one, nothing is blocked. Where a shortfall arises, qualifying quarter-hours before it are held, latest first (energy held for the shortest time), only as many as the shortfall needs and only where the quarter-hour's price is below the shortfall's price times the Battery Efficiency is NOT required (holding costs no conversion loss: a held kWh replaces a kWh bought later; hold when the quarter-hour's price is lower than the shortfall's price). Holding comes before buying: energy already stored is cheaper than energy bought with the efficiency loss.
- The BatteryLife Top-up window holds the battery too: while a top-up is wanted and the battery has to reach its level in this window, the battery is not discharged in quarter-hours in which the plan charges or between planned top-up quarter-hours (otherwise it would discharge what was just bought).
- The plan exposes per quarter-hour whether it is held; the House's Discharge Block follows the current quarter-hour of the plan instead of the Price Level alone. Without a plan (no prices, no capacity) there is no block.
- The sensor "Discharge Block" keeps its meaning (the discharge override is 0 W).

- [x] In a long cheap period without a later dearer shortfall the battery discharges
- [x] Before an expensive period the battery cannot carry, it is held in the cheap quarter-hours as far as needed
- [x] During a wanted top-up the battery is not discharged between its charging quarter-hours
- [x] Shortfall planning, top-up and Safety Buffer results stay consistent with the held quarter-hours
- [x] The spec and glossary say when a Discharge Block applies

## Comments

2026-11-13: Implemented; in the recreated 39 h cheap period nothing is held, before a dear evening only the latest cheap quarter-hours as far as needed. The buying step also holds the quarter-hours between a purchase and its shortfall.
