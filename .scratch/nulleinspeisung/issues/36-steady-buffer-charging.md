# 36: Charge steadily when the Safety Buffer applies, at the power the remaining cheap time needs

**What to build:** On 2026-11-10 from 21:30 both AC Batteries charged for the BatteryLife Top-up, each because of the Safety Buffer. The office house charged throughout; the living house charged every other quarter-hour (21:30, 22:00, 22:30, 23:00). The Safety Buffer asks whether the remaining qualifying quarter-hours would still be enough at half power; when the answer is "just", it charges the current quarter-hour at full power, which gains two half-power quarter-hours, so the next quarter-hour is not needed and the charging pauses. At every quarter-hour boundary the office house also fell back to "waiting" for about 0.3 s before charging went on.

The owner also pointed out that the prices after midnight are known and were cheap as well: the battery need not be charged at full power if it can be charged for longer at less power.

**Blocked by:** None (can start immediately)

**Status:** resolved

Decisions taken in planning:

- **Steady power instead of on/off.** While the Safety Buffer applies, the House charges at the power that covers the pessimistic case's need in the share of the remaining qualifying time the buffer leaves: for every deadline in the pessimistic plan (the end of the top-up window, the start of each shortfall), the energy needed by then divided by the qualifying hours from now to then, divided by (1 − buffer); the highest of these. At least a minimum charge power (500 W, below that the inverter/charger works poorly), at most the Maximum Charge Power. It is recomputed with every plan, so it changes slowly as the need and the time shrink.
- **Latch.** Once the Safety Buffer has started charging, it goes on through the following quarter-hours as long as the pessimistic case still needs energy and the quarter-hour qualifies, instead of being decided afresh at every boundary.
- **Top-up window over midnight.** The BatteryLife Top-up may be reached until the end of the run of qualifying quarter-hours that is going on at midnight (or begins before it and continues past it). Reaching 95 % at 01:00 counts for the new day, so a cheap night still gives one step of lowering per night; tomorrow's top-up is then already done. Without such a run the window ends at midnight as before.
- **Normal plan unchanged.** Planned quarter-hours that the normal plan chooses (cheapest first) still charge at the Maximum Charge Power. When both apply, the higher power wins.
- **No gap at the boundary.** A replan at a quarter-hour boundary must not release the setpoint override for a moment when the next quarter-hour is charged as well.

- [x] While the Safety Buffer applies, the battery charges continuously at a power between 500 W and the Maximum Charge Power, not in alternate quarter-hours
- [x] The power follows the need of the pessimistic case and the remaining qualifying time, including quarter-hours after midnight
- [x] The top-up window extends to the end of a qualifying run that crosses midnight
- [x] No short "waiting" between two charged quarter-hours
- [x] The House shows the charge power the Safety Buffer asks for
- [x] The rule is recorded in the spec

## Comments

2026-11-10: Implemented on branch `ticket-36-steady-buffer-charging`; 956 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. In the recreated living-house case the battery charges every quarter-hour at about 1.9 kW falling; with cheap quarter-hours after midnight about 1.1 kW. The 0.3 s "waiting" came from an evaluation at the boundary with the previous quarter-hour's plan; such an evaluation now replans first. New sensor `buffer_charge_power`. Not yet run on the real system.
