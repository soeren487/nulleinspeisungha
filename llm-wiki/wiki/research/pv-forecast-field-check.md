---
type: Research Finding
title: PV Forecast in the field - accuracy and what limits learning
description: Check of the PV Forecast against measured production on the owner's installation in November 2026 (release 0.12.0), how it enters the Charging Plan, and the three weaknesses found.
tags: [pv-forecast, grid-charging, field-data]
status: draft
generated: { by: claude-code/claude-fable-5-2, at: 2026-11-15T04:00:00Z }
stale_after: 2027-03-01T00:00:00Z
sources:
  - id: ha-recorder
    resource: Home Assistant recorder of the owner's installation, read through the HA MCP server on 2026-11-15
    title: Statistics and state history of the integration's sensors, 2026-10-25 to 2026-11-15
  - id: code
    resource: ../../../custom_components/nulleinspeisung/forecast.py
    title: forecast.py, forecast_model.py, forecast_source.py, grid_charging.py, charging_planner.py at 0.12.0
---

# Conclusion

The PV Forecast works: irradiance is fetched hourly, both Houses are usable and Grid Charging counts the forecast. Over three weeks the forecast is about 20 % above production, which the counted share of 70 % absorbs on average. Three weaknesses remain, none of them urgent in winter because PV is small against the load (2 to 6 kWh a day against 14 to 23 kWh).

# State of the remedies

All three weaknesses are addressed by ticket 43 (`.scratch/nulleinspeisung/issues/43-pv-forecast-field-fixes.md`), released in 0.12.1 on 2026-11-15. The sections below describe 0.12.0. To check after some weeks in the field: whether the pairs of factors appear (diagnostics, `component_factors_by_hour`; an hour needs 20 records with both parts that are not proportional) and whether the day-ahead ratio on dull days comes down. The settle time of 60 s after a curtailment is an estimate.

# How the forecast enters the Charging Plan

- Per quarter-hour the planner uses `Expected Load - share x PV Forecast - DC Battery Support`.[^code] A surplus charges the simulated battery, a deficit discharges it; what the battery cannot carry in a quarter-hour that does not qualify is a shortfall to be bought earlier.
- The forecast only counts production of PV Inverters; Battery-backed Inverters enter through the DC Battery Support.[^code]
- A quarter-hour without a forecast counts as 0 W, which errs towards buying more.[^code]
- Open-Meteo is asked for today and tomorrow in UTC. That always covers the known prices, so the plan never runs out of forecast.[^code]

# Measured accuracy (one House, November 2026)

Day-ahead value (the "tomorrow" sensor at about 23:20) against measured production of the PV Inverters:[^ha-recorder]

| Day | Day-ahead kWh | Measured kWh | Ratio |
| --- | --- | --- | --- |
| 11-06 | 7.2 | 5.7 | 1.3 |
| 11-07 | 3.9 | 1.7 | 2.3 |
| 11-08 | 2.3 | 1.9 | 1.2 |
| 11-09 | 4.4 | 3.1 | 1.4 |
| 11-10 | 4.4 | 2.1 | 2.1 |
| 11-11 | 3.7 | 1.5 | 2.4 |
| 11-12 | 4.4 | 3.9 | 1.1 |
| 11-13 | 3.8 | 4.8 | 0.8 |
| 11-14 | 5.3 | 6.0 | 0.9 |

- Sum: 39.4 forecast against 30.7 measured (1.28); with the 70 % share 27.6 counted (0.90).
- The error depends on the weather: dull days are overestimated by a factor of two and more, bright days underestimated by 10 to 20 %. The same pattern shows in the same-day forecast and for the other House.[^ha-recorder]
- On a dull day the share of 70 % still leaves about 1 kWh counted that does not come.

# Weaknesses

## 1. One factor per hour for all weather

The agent's inference: the model learns one factor per UTC hour on horizontal irradiance (`shortwave_radiation`). A tilted array turns direct light into much more power than diffuse light when the sun is low, so one factor is too high for overcast days and too low for clear ones. This was named as the weakness of the design in [PV forecast sources](pv-forecast-sources.md); the table above is its first measurement. Remedy without geometry: fetch `direct_radiation` and `diffuse_radiation` and learn two factors per hour.

## 2. A moment of curtailment or one failed poll discards the quarter-hour

- A quarter-hour is left out of learning when the House curtailed at any moment in it, and not recorded at all when one PV Inverter was unknown at any sample (a single failed DTU poll does that).[^code]
- On 2026-11-12 one House had about 20 curtailment episodes between sunrise and sunset, together some 30 minutes; they fall into most of the midday quarter-hours.[^ha-recorder]
- Effect: that House reached 8 usable quarter-hours on one day out of ten (history days 23 to 24 from 11-05 to 11-14), the other on every day but only around midday (27 to 37).[^ha-recorder] Learning goes on from the remaining records, which are mostly early and late ones, so the midday factors age.
- Remedy: drop the samples, not the quarter-hour, and record the quarter-hour when enough clean samples remain.

## 3. "Tomorrow" shows 0 kWh after midnight

From local midnight until the first fetch after 00:00 UTC the stored irradiance ends at 00:00 UTC of the new day, so the sensor for tomorrow sums only night slots and shows 0.0 instead of unknown.[^ha-recorder][^code] Display only: prices for that day are not known yet either.

# Not a problem

- A failed fetch is retried after 10 minutes and the stored irradiance is used meanwhile; one DNS failure on 2026-11-14 had no effect.[^ha-recorder]
- The forecast surplus is 0 kWh at this time of year, as expected.
