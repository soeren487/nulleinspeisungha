# 14: Charge the AC Battery in the cheapest quarter-hours

**What to build:** With Grid Charging switched on, a House computes a Charging Plan: the cheapest qualifying quarter-hours before sunrise that cover the energy missing to the Charge Target at Maximum Charge Power. In those quarter-hours it makes the AC Battery charge from the grid, using the mechanism decided in ticket 11. The plan is shown and kept current.

**Blocked by:** 12 (Hand the true Grid Power to the AC Battery), 13 (Fetch Tibber prices per House)

**Status:** ready-for-agent

- [ ] The House has a Grid Charging switch, a Charge Target number, and sensors for the Charging Plan and the energy to buy
- [ ] The plan picks the cheapest quarter-hours among the qualifying Price Levels up to sunrise, across midnight
- [ ] The plan is recomputed when prices arrive, every quarter-hour, and when the charge level deviates from it
- [ ] During a planned quarter-hour the battery charges at Maximum Charge Power; outside it the battery is released
- [ ] Switching Grid Charging off releases the battery immediately
- [ ] Without prices nothing is charged
- [ ] The planner is tested directly; execution is tested against a simulated broker
