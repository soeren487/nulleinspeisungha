# 28: Count the DC Batteries' night support when deciding how much to buy

**What to build:** Grid Charging assumes the AC Battery covers the whole night load alone. In fact each Battery-backed Inverter delivers power at night from the DC Batteries behind it, which the AC Battery then does not have to deliver. The owner tells each Battery-backed Inverter which sensors report the energy stored in its DC Batteries (one Inverter can have several: his four-input Inverters each have two). From that stored energy and from how the Inverter usually delivers at night, the House estimates the support until sunrise and buys correspondingly less. After a dull day with empty DC Batteries it counts no support.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] For each Battery-backed Inverter of a House the owner can choose any number of sensors that report stored energy, and change them later
- [x] The House shows the energy stored in its DC Batteries and the support it expects until sunrise
- [x] The expected support follows the Inverter's usual night output, learned from its own history, and never exceeds the stored energy
- [x] With full DC Batteries the Charging Plan buys less than without this ticket; with empty ones it buys the same
- [x] A sensor that is missing or unavailable counts as no stored energy; a House without such sensors behaves as before
- [x] The rule is recorded in the spec

## Comments

2026-10-07: Asked for by the owner. Implemented on branch `ticket-28-dc-battery-support`; 863 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. Per Battery-backed Inverter the owner names the sensors that report stored energy; the usual night output is the 80th percentile per quarter-hour over 21 days of the Inverter's own production, zero below three recorded days; the support stops when stored energy times an assumed conversion of 85 % is used up. It lowers the AC Battery's expected night drain. Limits: it changes the plan only on the path that uses the PV Forecast, so it has no effect until the forecast is usable; the 85 % is an assumption, not measured. A shared quarter-hour recorder now serves this history and the Expected Load; the PV Forecast recorder keeps its own.
