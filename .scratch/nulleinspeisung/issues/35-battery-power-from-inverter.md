# 35: Read the AC Battery's power from its inverter/charger, not from the battery monitor

**What to build:** The House reads the AC Battery's power from `system/0/Dc/Battery/Power`, which the GX takes from the battery monitor (the BMS). Its current has a resolution of about 1 A plus a few amperes of noise, so the power jumps by 50 to 200 W every second while the MultiPlus is idle and by up to 2.5 kW while it charges, although the grid stays steady. The battery power sensor, the House's consumption and the power flow on the owner's dashboard flutter with it. Curtailment, which counts a discharging battery as import, reads the noise as well.

The MultiPlus reports what it moves into or out of the battery itself in `system/0/Dc/Vebus/Power`; captured on 2026-10-25 it was 2 W with a standard deviation of 1 W while the battery monitor said -44 W with 90 W. The House reads that value instead, and falls back to the battery monitor's value only while the GX publishes no inverter/charger power.

**Blocked by:** None (can start immediately)

**Status:** resolved

Decisions taken in planning:

- Sign: positive while charging, as Victron's battery convention and as today. Still to be confirmed on the real GX while the battery charges; the release waits for that check.
- The inverter/charger's DC power leaves out small DC loads (the GX itself, a few watts). That is accepted.
- The battery monitor's value stays subscribed as the fallback; which one is in use is shown as an attribute `source` (`inverter` or `battery_monitor`) on the battery power sensor.

- [x] The battery power comes from `system/0/Dc/Vebus/Power` when the GX publishes it
- [x] Without it, the battery monitor's value is used, as before
- [x] The battery power sensor shows which source is in use
- [x] Curtailment, consumption and grid charging use the new value
- [x] The sign is confirmed on the real system before the release

## Comments

2026-10-25: Implemented on branch `ticket-35-inverter-dc-power`; 940 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. Open: the sign of `system/0/Dc/Vebus/Power` while charging, to be checked on the real GX before the release.

2026-10-25: The owner confirmed on the GX that `system/0/Dc/Vebus/Power` is positive while charging and negative while discharging.
