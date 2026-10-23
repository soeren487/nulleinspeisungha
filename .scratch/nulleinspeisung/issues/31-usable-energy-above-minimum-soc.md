# 31: Count only the AC Battery's energy above its minimum charge level

**What to build:** The Victron does not discharge its battery below a minimum charge level. With BatteryLife that minimum moves, and on the owner's installation it had risen to 75 % on both batteries, while Grid Charging counted the whole content (11.4 kWh at 76 %) as available for the night and decided to buy nothing. The House reads the minimum the Victron is applying right now and counts only the energy above it: for what the battery is expected to hold at sunrise and for what it can deliver until then. It shows the minimum, so the owner sees why little is usable.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] The House reads the minimum charge level the Victron applies right now, falling back to the configured minimum, and shows it
- [x] The battery content expected at sunrise counts only energy above that minimum, never below zero
- [x] With the battery at or below the minimum, the plan buys what the House needs at sunrise as if the battery were empty
- [x] A Victron that reports no minimum behaves as before
- [x] The rule is recorded in the spec

## Comments

2026-10-24: Found by reading the owner's installation: BatteryLife had raised the active minimum to 75 % on both GX devices while Grid Charging counted the whole content. Implemented on branch `ticket-31-usable-energy-above-minimum`; 923 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. The gateway reads the active and the configured minimum; the battery content expected at sunrise starts from the energy above the floor. In the recreated case the plan now buys 0.29 kWh where it bought nothing. Existing flow tests that use the recorded GX fixture (configured minimum 20 %) pin the minimum to none to keep their numbers.
