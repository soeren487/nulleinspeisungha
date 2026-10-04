# 18: Provide a PV Forecast per House

**What to build:** Each House fetches an irradiance forecast for its location and converts it to a PV Forecast per quarter-hour for today and tomorrow, using a conversion learned per hour of day from the House's own production history. Intervals in which the House was curtailing are left out of the learning. Until two weeks of history exist the forecast is marked as not usable.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** resolved

- [x] Irradiance for the House's location is fetched and refreshed several times a day
- [x] Production history is recorded per quarter-hour with a marker for curtailed intervals, and survives a restart
- [x] The PV Forecast for today and tomorrow is shown, together with whether it is usable
- [x] Curtailed intervals do not enter the learned conversion
- [x] Tests run against a simulated forecast service at the HTTP boundary; the learning is tested directly

## Comments

2026-10-05: Implemented on branch `ticket-18-pv-forecast`. 371 tests pass, linters clean. The client was run once against the real Open-Meteo service with generic coordinates. The forecast uses horizontal irradiance and one learned factor per UTC hour of day; it counts as usable once 14 days with at least 8 uncurtailed daylight quarter-hours each are recorded. Open: the history store of a removed House is not deleted.
