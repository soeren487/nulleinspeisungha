# 05: Apply House knowledge to stuck detection

**What to build:** Stuck detection uses what Houses know: Battery-backed Inverters are ignored when judging a DTU, and a DTU is also treated as stuck, at any sun angle, when an Inverter on another DTU of the same House is producing while this DTU has no fresh data.

**Blocked by:** 03 (Detect and restart a Stuck DTU), 04 (Create a House with Grid Meter and Inverters)

**Status:** resolved

- [x] A DTU whose only silent Inverters are Battery-backed is not restarted
- [x] A DTU with no fresh data is restarted below the sun angle when another DTU of the same House has a producing Inverter
- [x] A DTU serving two Houses is judged using both Houses

## Comments

2026-10-04: Implemented on branch `ticket-05-stuck-dtu-house-rules`. 143 tests pass, linters clean. The cross-DTU rule was narrowed: the producing Inverter elsewhere must be a PV Inverter with fresh data delivering at least 50 W. Without that, a Battery-backed Inverter producing at night would make the House's other DTUs look stuck, and roofs waking at different times would trigger restarts at dawn.
