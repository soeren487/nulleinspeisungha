---
type: Research Finding
title: PV forecast sources and how schedulers use them for grid charging
description: Forecast sources usable from a Home Assistant custom integration in Germany, their limits and licences, geometry-free calibration from production history, and the rule that turns PV and load forecasts into a grid charging amount.
tags: [pv-forecast, open-meteo, forecast-solar, solcast, dwd, grid-charging, home-assistant]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T09:36:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: om-docs
    resource: https://open-meteo.com/en/docs
    title: Open-Meteo forecast API documentation (fetched 2026-10-04)
  - id: om-dwd
    resource: https://open-meteo.com/en/docs/dwd-api
    title: Open-Meteo DWD ICON API documentation (fetched 2026-10-04)
  - id: om-terms
    resource: https://open-meteo.com/en/terms
    title: Open-Meteo terms, pricing and licence pages (terms, /en/pricing, /en/licence; fetched 2026-10-04)
  - id: om-live
    resource: https://api.open-meteo.com/v1/forecast
    title: Live test call by the agent, 2026-10-04 09:36 UTC (lat 51.0, lon 9.0, models=icon_d2, tilt=30, azimuth=-90, minutely_15 and hourly global_tilted_irradiance)
  - id: om-blog
    resource: https://openmeteo.substack.com/p/enhanced-solar-radiation-forecasts
    title: Open-Meteo blog - Enhanced solar radiation forecasts with 15-minute intervals (2023-03) and Improved solar radiation forecast (2022-07)
  - id: fs-docs
    resource: https://doc.forecast.solar/account_models
    title: Forecast.Solar documentation - account models, api:estimate, actual (fetched 2026-10-04)
  - id: solcast-docs
    resource: https://docs.solcast.com.au/docs/section/rooftop-sites-hobbyist
    title: Solcast API docs - Hobbyist users, and knowledge base "Home hobbyists FAQs" (fetched 2026-10-04)
  - id: brightsky
    resource: https://api.brightsky.dev/openapi.json
    title: Bright Sky OpenAPI description and README; live test call 2026-10-04
  - id: dwd-mosmix
    resource: https://www.dwd.de/EN/ourservices/met_application_mosmix/met_application_mosmix.html
    title: DWD - MOSMIX product description, and DWD legal notice (CC BY 4.0)
  - id: ha-forecast-solar
    resource: https://www.home-assistant.io/integrations/forecast_solar/
    title: Home Assistant - Forecast.Solar integration, and core source forecast_solar/energy.py, energy/types.py, energy/websocket_api.py (dev branch)
  - id: ha-om-solar
    resource: https://github.com/rany2/ha-open-meteo-solar-forecast
    title: rany2/ha-open-meteo-solar-forecast (README, sensor.py, energy.py; master)
  - id: ha-solcast
    resource: https://github.com/BJReplay/ha-solcast-solar
    title: BJReplay/ha-solcast-solar README
  - id: emhass
    resource: https://emhass.readthedocs.io/en/latest/forecasts.html
    title: EMHASS documentation - the forecast module
  - id: predbat
    resource: https://springfall2008.github.io/batpred/customisation/
    title: Predbat documentation - customisation, apps.yaml, install, what-does-predbat-do
  - id: evcc
    resource: https://docs.evcc.io/en/features/battery
    title: evcc documentation - battery features, and tariffs (solar forecast providers)
  - id: dess
    resource: https://www.victronenergy.com/live/drafts:dynamic_ess
    title: Victron - Dynamic ESS manual (page last modified 2026-09-04), and community summaries
  - id: interview
    resource: ../project/requirements-decisions.md
    title: Requirements decisions
---

# Conclusions

- **Open-Meteo is the only source that fits without compromises**: no key, home automation named as allowed non-commercial use, 10,000 calls a day, DWD ICON-D2 data at 15-minute resolution for today and tomorrow, and either plane-of-array irradiance for a given tilt and azimuth or plain horizontal irradiance.[^om-terms][^om-dwd][^om-live]
- **Forecast.Solar free** allows one plane per call and 12 calls per hour per IP, shared with any other user of it in the same household; several orientations per house need a paid plan.[^fs-docs] **Solcast hobbyist** allows 2 sites within 1 km and 10 calls per UTC day, needs a key, and no longer offers tuning.[^solcast-docs] **DWD MOSMIX via Bright Sky** is free and keyless but gives only horizontal irradiation at the nearest forecast station.[^brightsky]
- **Geometry can be avoided.** EMHASS, Predbat and the Solcast integration all correct a provider forecast with the system's own production history, and all three warn that curtailed periods must be excluded.[^emhass][^predbat][^ha-solcast] This project curtails by design, so that exclusion is mandatory here; the integration knows when it is limiting.
- **No published accuracy figure was found for any of the four sources at a German rooftop.** Vendor statements are qualitative. How well a learned factor works is reported only through each tool's own error metric, not as a published number.
- **Simplest sound charging rule (agent's formulation):** charge only the largest cumulative shortfall of load over PV between sunrise and the next cheap period, never more than the battery can hold. See below.

# 1. Sources

| | Open-Meteo | Forecast.Solar (free "Public") | Solcast hobbyist | DWD MOSMIX via Bright Sky |
| --- | --- | --- | --- | --- |
| Returns | Irradiance in W/m²: `shortwave_radiation` (horizontal), `direct_radiation`, `diffuse_radiation`, `direct_normal_irradiance`, `global_tilted_irradiance`, plus temperature, cloud cover.[^om-docs] Not PV power; the caller converts | PV power and energy: `watts`, `watt_hours_period`, `watt_hours`, `watt_hours_day`[^fs-docs] | PV power in kW per 30 min with 10th, 50th, 90th percentile; also "estimated actuals"[^solcast-docs][^ha-solcast] | `solar`: "Solar irradiation during previous 60 minutes", kWh/m², horizontal; `sunshine`, `cloud_cover`[^brightsky] |
| Inputs | Latitude, longitude; for tilted irradiance `tilt` (0 to 90) and `azimuth` ("0° south, -90° east, 90° west, ±180 north"), one plane per request[^om-docs] | Latitude, longitude, declination 0 to 90, azimuth (-180 north, -90 east, 0 south, 90 west), kWp; one plane per call[^fs-docs] | Sites are defined in the Solcast web toolkit: capacity, azimuth, tilt, install date, efficiency factor[^solcast-docs] | Latitude, longitude, date[^brightsky] |
| Resolution | Hourly; 15-minute from ICON-D2 in Central Europe. Hourly radiation is the mean of the preceding hour, 15-minute radiation the mean of the 15 minutes[^om-dwd] | 1 hour (30 and 15 min on paid plans)[^fs-docs] | 30 min[^solcast-docs] | 1 hour[^brightsky] |
| Horizon | Up to 16 days; ICON-D2 2 days, ICON-EU 5 days[^om-docs][^om-dwd] | "1-2 days" (today and tomorrow); 3 to 6 on paid plans[^fs-docs] | Up to 14 days[^solcast-docs] | 240 h[^dwd-mosmix] |
| Model updates | ICON-D2 and ICON-EU every 3 h, ICON global every 6 h[^om-dwd] | Not stated in the documentation read | Not stated; EMHASS says the hobbyist forecast "will be updated every 6 hours"[^emhass] | MOSMIX_S 24 times a day, MOSMIX_L 4 times[^dwd-mosmix] |
| API key | None on the free tier[^om-terms] | None[^fs-docs] | Required[^solcast-docs] | None[^brightsky] |
| Free limits | 600 per minute, 5,000 per hour, 10,000 per day, 300,000 per month; requests with more than 10 variables or more than 2 weeks count as several calls[^om-terms] | 12 calls per IP per 60 minutes[^fs-docs] | "Up to 10 requests per UTC day", "Up to 2 rooftop sites (arrays) within 1 km of each other"[^solcast-docs] | Not documented |
| Licence | Free tier is non-commercial; acceptable uses listed include personal sites and home automation. Data CC BY 4.0, attribution "Weather data by Open-Meteo.com"[^om-terms] | API terms for the public tier not found in the documentation | "personal use"[^solcast-docs] | "The DWD's Terms of Use apply"; DWD open data is CC BY 4.0 with source acknowledgement[^brightsky][^dwd-mosmix] |
| Self-calibration | None | `?actual=` corrects today only, paid plans only[^fs-docs] | "PV Tuning Discontinued"[^solcast-docs] | None |

Notes:

- Open-Meteo live test: `models=icon_d2&minutely_15=global_tilted_irradiance&tilt=30&azimuth=-90&forecast_days=2` returned 192 quarter-hour values in W/m² through the end of tomorrow.[^om-live] The 15-minute resolution matches Tibber quarter-hours.[^interview]
- Open-Meteo call budget (arithmetic): two houses with three planes each, refreshed hourly, are 144 calls a day, 1.4 % of the daily limit.
- Azimuth conventions differ: Open-Meteo and the Forecast.Solar API use 0 = south; the Home Assistant Forecast.Solar and Open-Meteo Solar Forecast config flows use 0 = north, 180 = south.[^om-docs][^fs-docs][^ha-forecast-solar][^ha-om-solar]
- Solcast advises users with more than two arrays to enter one site with the average azimuth.[^solcast-docs] Two houses with several orientations each exceed the hobbyist account unless they lie within 1 km and each is entered as one averaged site (inference).
- Bright Sky live test returned the MOSMIX station 4.8 km from the test point; the value is a station point forecast, not a grid cell for the address.[^brightsky] MOSMIX is statistically post-processed from ICON and ECMWF IFS.[^dwd-mosmix]
- Redistribution through HACS: every user calls the API from their own IP for their own home, which is the personal use the Open-Meteo and Solcast terms describe (agent's reading, not a statement by either provider). Forecast.Solar's terms for the keyless tier could not be established.

## Accuracy

- Open-Meteo: qualitative only. Forecasts became "a bit more accurate" with clear-sky interpolation (2022); the ICON-D2 15-minute post cites DWD verification charts without numbers.[^om-blog]
- Solcast: an integration-side "Accuracy" sensor reports MAPE per installation once auto-dampening runs; no hobbyist-tier figure is published in the pages read.[^ha-solcast]
- Forecast.Solar, MOSMIX: no accuracy statement found.
- Forum threads complaining that Forecast.Solar or Victron's VRM forecast are far off exist (anecdote, not evaluated).

# 2. Home Assistant integrations

| Integration | Kind | Entities and interfaces |
| --- | --- | --- |
| Forecast.Solar | Core | Sensors: estimated energy production today, remaining today, tomorrow, this hour, next hour; power now; peak time today and tomorrow. Free accounts update hourly. Several planes need a paid account.[^ha-forecast-solar] |
| Open-Meteo Solar Forecast (rany2) | Custom (HACS), Apache-2.0 | Fork of the Forecast.Solar integration. Sensors `energy_production_today`, `_today_remaining`, `_tomorrow`, `_d2` to `_d7`, `power_production_now` and others; the energy sensors carry attributes `watts`, `wh_period`, `wh_period_15m`. Config per array: location, azimuth, declination, module power, DC efficiency (about 0.93), damping, horizon file, inverter size.[^ha-om-solar] |
| Solcast PV Forecast (BJReplay) | Custom (HACS) | "Forecast Today", "Forecast Tomorrow" up to day 7, remaining today, power now; attributes `detailedForecast` and `detailedHourly` with `estimate10`, `estimate`, `estimate90`; actions `solcast_solar.query_forecast_data` and `query_estimate_data`.[^ha-solcast] |

The energy platform is the common interface. An integration ships `energy.py` with `async_get_solar_forecast(hass, config_entry_id)` returning `{"wh_hours": {<ISO timestamp>: <Wh>}}`.[^ha-forecast-solar] Core Forecast.Solar and the Open-Meteo fork implement it; a Solcast `energy.py` exists in the repository (HTTP 200, content not read).[^ha-om-solar] Home Assistant collects these in `async_get_energy_platforms(hass)` and serves them through the websocket command `energy/solar_forecast`; there is no service action, and the collector is not documented as a public API.[^ha-forecast-solar] Another integration could call the platform function for a config entry the user picks, or read `energy_production_tomorrow` and the `wh_period` attribute (inference; the first depends on an internal function).

# 3. Avoiding panel geometry

| Project | What it does | Caveat it states |
| --- | --- | --- |
| EMHASS | Fits a regression (Lasso, random forest, gradient boosting and others) on "historical forecasted and actual PV production data", with "time of day and solar angles" as features, then corrects new forecasts; reports RMSE and R².[^emhass] | Curtailed timesteps are excluded from training, "with a one-timestep margin on either side".[^emhass] |
| Predbat | On by default: uses "historical solar generation data to calibrate your PV production estimates on a slot duration basis", comparing actual production with the provider's raw forecast. Weights the pessimistic scenario at 0.15 by default.[^predbat] | Disable "if your export generation can be curtailed", otherwise the calibration "will significantly reduce your forecast PV generation".[^predbat] |
| Solcast integration | Automated dampening: per half-hour factors from "up to fourteen 'rolling' days of generation and estimated generation data"; takes the highest actual generation among comparable periods.[^ha-solcast] | Optional export-limit detection "to exclude artificial curtailment periods".[^ha-solcast] |
| Victron Dynamic ESS | Applies weather-forecast scaling to solar capacity learned from historical production; no geometry is entered (community summary, not the manual).[^dess] | Forum complaints of forecasts several times too high (anecdote). |
| evcc | Takes forecasts from Forecast.Solar, Open-Meteo, Solcast, pvnode, Victron VRM or a custom source; has a CLI `evcc metrics forecast` comparing forecast and production.[^evcc] | Whether evcc scales the forecast automatically could not be established from the pages reachable. |
| OpenDTU-OnBattery | No forecast feature found in its documentation or by search. | Not established either way. |

All of these start from a provider forecast that already contains geometry. A fully geometry-free variant, the agent's proposal and untested: take horizontal irradiance for the address, and learn per house a factor per hour-of-day bin, `production_Wh / irradiance_Wh_per_m2`, over a rolling 14 to 30 days, using only intervals in which no inverter was limited below 100 % and all inverters reported. The hour-of-day bins absorb orientation and shading; the rolling window absorbs the season. Weakness: a tilted array converts direct and diffuse light differently, so a single factor per bin is biased between clear and overcast days; fitting two factors per bin (direct, diffuse) addresses that. No source quantifies the resulting error.

# 4. How schedulers size grid charging

| Scheduler | Method | Consumption estimate |
| --- | --- | --- |
| EMHASS | Optimisation over the forecast horizon from four forecasts: PV, load, purchase price, sale price.[^emhass] | Default `typical`: statistics from "a year long load power data grouped by the current day-of-the-week of the current month" from a bundled data set, scaled by maximum grid power. Alternatives: naive persistence (same period 24 h earlier) or an ML forecaster with lag features.[^emhass] |
| Predbat | Cost search over charge windows and target charge levels; plans "to achieve the best (lowest) cost".[^predbat] | Weighted average of all recorded history per time slot: weekday match 1.0, same type of day 0.7, other 0.5; age factor 0.9 for yesterday falling 0.03 per day to 0.1; empty slots ignored; multiplied by `load_scaling` 1.05.[^predbat] |
| evcc | No forecast in the decision: below a price limit "the home battery will be charged from the grid and simultaneously locked to prevent discharging", up to a maximum charge level.[^evcc] | None. |
| Victron Dynamic ESS | Schedule computed in VRM from prices, solar and consumption forecasts and battery cost; a target charge level per hour sent to the GX.[^dess] | "Historical usage patterns"; window not stated.[^dess] |
| Tibber | Not established. No technical description of how Tibber's own battery steering sizes a charge was found. | Not established. |

## Simplest sound rule (agent's formulation)

Planned once prices are known and replanned as the charge level changes, as already decided for the slots.[^interview]

1. Horizon: from the deadline (sunrise) to the start of the next run of cheap quarter-hours, at most 24 h.
2. For each quarter-hour `t` in the horizon: `net[t] = load[t] - k * pv[t]`, with `k` a caution factor below 1 (Predbat's pessimistic weighting serves the same purpose).[^predbat]
3. `need = max over t of the running sum of net[sunrise..t]`, floored at 0. This is the most energy the battery must hold at sunrise to carry the house until PV, or the next cheap period, takes over. On a sunny day the running sum peaks mid-morning and is small; on a dull day it peaks in the evening and is large.
4. `target = min(configured target, reserve + need / discharge efficiency)`, capped at usable capacity.
5. `charge from grid = max(0, target - energy in battery now + expected draw until the deadline) / charge efficiency`. The discharge block makes the expected draw zero in cheap quarter-hours.[^interview]
6. Fill that amount into the cheapest allowed quarter-hours at maximum charge power, as decided.[^interview]

Inputs: usable capacity and current charge level per house; PV forecast per quarter-hour; load forecast per quarter-hour; reserve; efficiencies (a single round-trip figure is enough). Load history needs no extra sensor: house load = grid power + inverter production - battery charge power, all of which the integration already handles (inference). The Predbat averaging above is the simplest documented load estimate that respects weekdays. The price check that grid charging pays at all is already covered by restricting slots to cheap price levels.[^interview]

# Recommendation (claude-opus-5-5)

1. Use **Open-Meteo directly** from the integration, `models=icon_d2` with ICON-EU or the default blend as fallback, 15-minute values, refreshed about hourly, with attribution in the documentation. It is the only source with no key, no registration, enough free calls for many arrays, and explicit mention of home automation.
2. Per house ask only for **latitude and longitude** (default: the Home Assistant home location). Derive the conversion from irradiance to watts from OpenDTU production history as described in section 3, excluding curtailed intervals. Until about two weeks of history exist, fall back to `sum of inverter rated power x horizontal irradiance / 1000 x 0.75` (a rough starting value, agent's estimate).
3. Offer **optional tilt and azimuth per inverter** for users who want a correct forecast from day one; this uses `global_tilted_irradiance` and one request per plane.
4. Publish the result through an `energy.py` platform and as sensors, so the Energy dashboard shows it.
5. Do not depend on another forecast integration; optionally accept an existing forecast entity as an alternative source later.
6. Treat the learned factor as unproven: log forecast against actual production per day from the first release and review after a month.

[^om-docs]: Open-Meteo forecast API documentation (fetched 2026-10-04)
[^om-dwd]: Open-Meteo DWD ICON API documentation (fetched 2026-10-04)
[^om-terms]: Open-Meteo terms, pricing and licence pages (fetched 2026-10-04)
[^om-live]: Live test call by the agent, 2026-10-04 09:36 UTC
[^om-blog]: Open-Meteo blog - Enhanced solar radiation forecasts with 15-minute intervals (2023-03) and Improved solar radiation forecast (2022-07)
[^fs-docs]: Forecast.Solar documentation - account models, api:estimate, actual (fetched 2026-10-04)
[^solcast-docs]: Solcast API docs - Hobbyist users, and knowledge base "Home hobbyists FAQs" (fetched 2026-10-04)
[^brightsky]: Bright Sky OpenAPI description and README; live test call 2026-10-04
[^dwd-mosmix]: DWD - MOSMIX product description, and DWD legal notice (CC BY 4.0)
[^ha-forecast-solar]: Home Assistant - Forecast.Solar integration, and core source forecast_solar/energy.py, energy/types.py, energy/websocket_api.py (dev branch)
[^ha-om-solar]: rany2/ha-open-meteo-solar-forecast (README, sensor.py, energy.py; master)
[^ha-solcast]: BJReplay/ha-solcast-solar README
[^emhass]: EMHASS documentation - the forecast module
[^predbat]: Predbat documentation - customisation, apps.yaml, install, what-does-predbat-do
[^evcc]: evcc documentation - battery features, and tariffs (solar forecast providers)
[^dess]: Victron - Dynamic ESS manual (page last modified 2026-09-04), and community summaries
[^interview]: Requirements decisions
