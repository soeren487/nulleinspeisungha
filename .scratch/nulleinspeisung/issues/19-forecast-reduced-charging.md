# 19: Buy only what the sun will not deliver

**What to build:** Grid Charging uses the PV Forecast and the Expected Load to work out the energy the battery must hold at sunrise, and buys only up to that, never more than the energy missing to the Charge Target. Only a configurable share of the forecast is counted. While the forecast is not usable, Grid Charging fills to the Charge Target as before.

**Blocked by:** 14 (Charge the AC Battery in the cheapest quarter-hours), 17 (Learn the Expected Load), 18 (Provide a PV Forecast per House)

**Status:** resolved

- [x] The energy needed at sunrise is the peak of the running sum of Expected Load minus counted PV Forecast, from sunrise to the next qualifying price period
- [x] The Charging Plan covers the smaller of that and the energy missing to the Charge Target
- [x] The counted share is configurable per House, default 70 %
- [x] With an unusable forecast the plan equals the one from ticket 14
- [x] The House shows the forecast surplus and the energy needed at sunrise
- [x] The calculation is tested directly

## Comments

2026-10-06: Implemented on branch `ticket-19-forecast-reduced-charging`. 801 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. A pure module computes the energy needed at sunrise (peak of the running sum of Expected Load minus the counted share of the PV Forecast, until the next qualifying price period, divided by the discharging half of the Battery Efficiency) and the battery content expected at sunrise; the difference bounds what the planner may buy. Used only when the forecast is usable and the owner's switch is on; otherwise the plan is the one of ticket 14. Simplification: night production of Battery-backed Inverters is not counted. Also fixed here: two tests in the failure flow depended on the order of DTU poll and control step.
