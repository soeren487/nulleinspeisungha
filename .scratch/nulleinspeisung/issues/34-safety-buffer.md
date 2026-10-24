# 34: Safety Buffer against a PV Forecast that does not come true

**What to build:** Grid Charging counts the PV Forecast and, on equal prices, charges in the latest qualifying quarter-hours. Both are right when the forecast comes true: the sun fills the battery first, nothing is curtailed, and the grid buys only the rest. When the sun stays away, the plan notices only during the day, and by then few qualifying quarter-hours may be left. Seen on 2026-10-25: prices were 13.47 ct from 02:15 to 15:15, the BatteryLife Top-up was planned for 15:00 to 15:30 only, counting on 7.3 kWh of forecast; a cloudy day would have left three quarter-hours to reach 95 %.

The House therefore also checks, in every planning run, pessimistic cases with only part of the Maximum Charge Power available (and, for the top-up, no PV at all from now on). If in that case the BatteryLife Top-up or a shortfall could only be covered by charging in the current quarter-hour, it charges now, even though the normal plan would wait.

**Blocked by:** None (can start immediately)

**Status:** resolved

Decisions taken in planning:

- There are two pessimistic runs, both the same planning with the same efficiency test on the same quarter-hours and with the Maximum Charge Power reduced by the Safety Buffer: (1) the BatteryLife Top-up alone with the PV Forecast set to zero (DC Battery Support and Expected Load stay); (2) the top-up and the shortfalls with the counted PV Forecast as given. For shortfalls the buffer is thus a time margin at reduced power, not a bet against the forecast: with no PV, a cheap night before an expensive sunny day would always be charged in and the sun's room lost (review, 2026-10-25). The current quarter-hour gets the larger of the two amounts.
- In the pessimistic run the candidates are taken latest first, whatever their price (the running quarter-hour still first). It asks "when must charging start at the latest?", not "where is it cheapest?". The current quarter-hour is therefore only used when the later qualifying quarter-hours are not enough.
- Only the current quarter-hour is taken over from the pessimistic runs into the plan. The rest of the plan stays the normal one, so the sun still has the room it is expected to fill.
- The Safety Buffer is a number per House in percent of the Maximum Charge Power held back in the pessimistic run, 0 to 90 %, default 50 %. At 50 % charging starts at the latest when the remaining qualifying quarter-hours, at half power, are just enough (for the top-up without sun). At 0 % the top-up run still assumes no sun, at full power.
- The House shows when the Safety Buffer is what makes it charge now.

- [x] When the remaining qualifying quarter-hours, at the reduced power and without PV, are only just enough for the top-up or a shortfall, the current quarter-hour is charged in
- [x] When they are more than enough, the plan is the normal one (latest cheapest quarter-hours, PV counted)
- [x] The pessimistic run follows the Charge Target, the efficiency test for shortfalls, Discharge Blocks and the DC Battery Support
- [x] A number per House sets the Safety Buffer, default 50 %
- [x] A binary sensor per House shows that the Safety Buffer makes it charge now
- [x] The rule is recorded in the spec and the glossary

## Comments

2026-10-25: Implemented on branch `ticket-34-safety-buffer`; 937 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. After review the PV-free assumption was narrowed to the top-up: for shortfalls the pessimistic run keeps the counted forecast and is only a time margin at reduced power, because a no-PV case would charge every cheap night before a sunny expensive day. New number `safety_buffer` (50 %) and binary sensor `safety_buffer_active`. Not yet run on the real system.
