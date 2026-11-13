# 40: Say why, and offer diagnostics

**What to build:** The owner cannot see why the House charges, holds or waits; the review of 2026-11-13 had to reconstruct it from history. Two things:

1. A sensor per House, "Grid charging reason" ("Begründung Netzladen"), whose state is a short code and whose attribute `text` is one sentence in the owner's language-neutral English built from the plan: what the House does in the current quarter-hour and why (for example: charging 1.2 kW for the BatteryLife top-up because the Safety Buffer applies; holding the battery for a shortfall of 1.4 kWh at 19:00 at 31 ct; waiting, next charging 02:30; nothing to do, no shortfall until the prices end at 23:45). The states are translated; the numbers are attributes (`next_start`, `shortfall_kwh`, `shortfall_at`, `shortfall_price`, `held_until`, `power`).
2. The plan as data: the sensor "Next charging start" already lists the slots; add attributes `held` (the held quarter-hours) and, per slot, the grid energy and whether it belongs to the top-up, so that a dashboard chart can draw charging and holding over the price curve.
3. Home Assistant's "Download diagnostics" for the config entry: settings, each House's inputs to the last plan (quarters with price, level, load, PV, support), the plan, the battery state, the DTU states; with addresses, host names, portal ids, serials, tokens and passwords redacted.

**Blocked by:** 37, 38

**Status:** resolved

- [x] The reason sensor shows a state and a sentence for each of: off, not in control, battery unavailable, no prices, charging (plan / top-up / safety buffer), holding, waiting, nothing to do
- [x] The plan's slots and held quarter-hours are available as attributes
- [x] Diagnostics can be downloaded and contain nothing that identifies the installation

## Comments

2026-11-13: Implemented: sensor `charging_reason`, attributes `plan` and `held` on the next-charging-start sensor, diagnostics. 1026 tests pass on the pinned Home Assistant and on 2026.9.4.
