# 19: Buy only what the sun will not deliver

**What to build:** Grid Charging uses the PV Forecast and the Expected Load to work out the energy the battery must hold at sunrise, and buys only up to that, never more than the energy missing to the Charge Target. Only a configurable share of the forecast is counted. While the forecast is not usable, Grid Charging fills to the Charge Target as before.

**Blocked by:** 14 (Charge the AC Battery in the cheapest quarter-hours), 17 (Learn the Expected Load), 18 (Provide a PV Forecast per House)

**Status:** ready-for-agent

- [ ] The energy needed at sunrise is the peak of the running sum of Expected Load minus counted PV Forecast, from sunrise to the next qualifying price period
- [ ] The Charging Plan covers the smaller of that and the energy missing to the Charge Target
- [ ] The counted share is configurable per House, default 70 %
- [ ] With an unusable forecast the plan equals the one from ticket 14
- [ ] The House shows the forecast surplus and the energy needed at sunrise
- [ ] The calculation is tested directly
