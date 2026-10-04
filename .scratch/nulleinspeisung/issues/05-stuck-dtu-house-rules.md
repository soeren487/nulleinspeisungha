# 05: Apply House knowledge to stuck detection

**What to build:** Stuck detection uses what Houses know: Battery-backed Inverters are ignored when judging a DTU, and a DTU is also treated as stuck, at any sun angle, when an Inverter on another DTU of the same House is producing while this DTU has no fresh data.

**Blocked by:** 03 (Detect and restart a Stuck DTU), 04 (Create a House with Grid Meter and Inverters)

**Status:** ready-for-agent

- [ ] A DTU whose only silent Inverters are Battery-backed is not restarted
- [ ] A DTU with no fresh data is restarted below the sun angle when another DTU of the same House has a producing Inverter
- [ ] A DTU serving two Houses is judged using both Houses
