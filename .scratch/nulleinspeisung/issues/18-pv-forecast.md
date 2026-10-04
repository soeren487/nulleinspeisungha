# 18: Provide a PV Forecast per House

**What to build:** Each House fetches an irradiance forecast for its location and converts it to a PV Forecast per quarter-hour for today and tomorrow, using a conversion learned per hour of day from the House's own production history. Intervals in which the House was curtailing are left out of the learning. Until two weeks of history exist the forecast is marked as not usable.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** ready-for-agent

- [ ] Irradiance for the House's location is fetched and refreshed several times a day
- [ ] Production history is recorded per quarter-hour with a marker for curtailed intervals, and survives a restart
- [ ] The PV Forecast for today and tomorrow is shown, together with whether it is usable
- [ ] Curtailed intervals do not enter the learned conversion
- [ ] Tests run against a simulated forecast service at the HTTP boundary; the learning is tested directly
