# 43: PV Forecast: fixes from the field check of 2026-11-15

**What to build:** Three corrections of the PV Forecast found by comparing it with measured production (wiki: `llm-wiki/wiki/research/pv-forecast-field-check.md`). Work in the order given; B builds on the cleaner records of A.

**Blocked by:** none

**Status:** resolved

## A. Drop samples, not quarter-hours

Today one moment of curtailment marks the whole quarter-hour as curtailed (left out of learning), and one sample with an unknown PV Inverter (a single failed DTU poll) keeps it from being recorded at all. One House had about 20 short curtailment episodes on a November day and lost most midday quarter-hours.

- [x] A sample is *clean* when every PV Inverter is known, the House is not curtailing a PV Inverter at that moment, and at least `CURTAIL_SETTLE` (60 s, a constant with a docstring saying it is an estimate of the ramp back to full power) has passed since it last did.
- [x] Only a limit below 100 % on a **PV Inverter** counts. A limit on a Battery-backed Inverter alone does not hold PV production back. Add what is needed to `HouseControl` (it already tells the two groups apart) and leave the existing `curtailing` as it is for its other users.
- [x] `HouseForecast` keeps two accumulations per quarter-hour: all samples with a known production (as today) and the clean ones. Time is credited to a sample as today (at most `MAX_SAMPLE_COVER`), to the clean accumulation only for clean samples; the time before a sample that is not clean is credited to nobody.
- [x] At the end of the quarter-hour: clean cover of at least `MIN_COVERED` records the mean of the clean samples with `curtailed=False`; otherwise cover of all known samples of at least `MIN_COVERED` records their mean with `curtailed=True` (as today); otherwise nothing. The `bad` flag goes away: unknown samples simply cover nothing.
- [x] Tests: a quarter-hour with a short curtailment episode is recorded clean with the mean of the clean samples only; samples inside the settle time are left out; a failed poll in the middle no longer loses the quarter-hour; a quarter-hour curtailed for more than half is recorded as curtailed; a limit on a Battery-backed Inverter alone changes nothing.

## B. Two factors per hour: direct and diffuse

One factor per UTC hour on horizontal irradiance overestimates dull days by a factor of two and underestimates clear ones (a tilted array turns direct light into more power than diffuse light when the sun is low).

- [x] `forecast_source`: ask Open-Meteo for `shortwave_radiation,direct_radiation,diffuse_radiation` in `minutely_15`. `IrradiancePoint` gains `direct` and `diffuse` (W/m², `None` when the answer has no value there). Make one live call with generic coordinates (latitude 51.0, longitude 9.0, never the owner's) to confirm the field names and that direct plus diffuse is about the shortwave value; base the test fixture on that answer.
- [x] `HistoryRecord` gains `direct` and `diffuse`, both optional. The store writes six values per row and still reads rows of four (the existing history, components unknown). No history is lost by the update.
- [x] `learn` keeps the single factor per hour and overall exactly as today, and additionally fits per UTC hour `production ≈ a_direct × direct + a_diffuse × diffuse` by weighted least squares through the origin (same age weights), over participating records that have both components. The pair of an hour is used only when it has at least `MIN_SPLIT_RECORDS` (20) such records and the two components are not close to proportional (determinant of the normal equations at least 5 % of the product of its diagonal; constant with docstring). A negative coefficient is set to 0 and the other refitted alone.
- [x] `production_for` and `convert` use the pair when the hour has one and the point has both components, else the single factor on the shortwave value. Never negative.
- [x] Everything that reads the factors or builds records keeps working: sensors, diagnostics (show the pairs), `tools/` (replay and export), tests.
- [x] Tests for the fit: synthetic records made from known coefficients are recovered; proportional components and too few records fall back to the single factor; old records without components still feed the single factor; negative solution is clamped; a store with four-value rows loads and is saved with six.

## C. "PV forecast tomorrow" shows 0.0 kWh after midnight

From local midnight until the first fetch after 00:00 UTC the stored irradiance ends about an hour into tomorrow, so the sensor sums night slots only and shows 0.0.

- [x] Ask for `forecast_days=3`.
- [x] `HouseForecast.energy_kwh(day)` returns `None` unless the known slots reach the end of that local day (last slot start at or after the day's end minus one quarter-hour).
- [x] Tests: at 00:10 local with irradiance for two UTC days the value of tomorrow is unknown, not 0; with three days it is the sum; today after a start in the afternoon still has a value.

## Also

- [x] CI: remove the `hacs/action` step (the owner decided on 2026-11-15 against a licence and repository topics, which that check demands); the job keeps hassfest and is named for it.
- [x] `CHANGELOG.md` under Unreleased, `README.md` where it describes the PV Forecast, `CONTEXT.md` if a term changes.
- [x] `uv run pytest -q` and `uv run ruff check . && uv run ruff format --check .` pass.

## Comments

2026-11-15 (implementation): A, B, C and "Also" are done.

- A: `HouseControl.curtailing_pv` (limit below 100 % on a PV Inverter, while Curtailment is on); `curtailing` is unchanged. `HouseForecast` keeps `_curtailed_at` (last moment seen curtailing, set in `_observe` and `_handle_control`) and two accumulations per quarter-hour; `bad` and the slot's `curtailed` flag are gone, `CURTAIL_SETTLE = 60` s.
- B: `IrradiancePoint` moved to `forecast_model` (so that module stays free of Home Assistant) and is re-used by `forecast_source`. `Factors.split_by_hour` holds the pairs (`ComponentFactors`). The store writes six values and reads four or six. Diagnostics gained a `forecast` block per House (usable, history days, record counts, factor per hour, pair per hour). `tools/` read neither the history store nor the factors, so nothing changed there.
- C: `forecast_days=3`; `energy_kwh` is `None` unless the last known slot starts at or after the end of the local day minus one quarter-hour.
- Live Open-Meteo call (latitude 51.0, longitude 9.0): the fields are `shortwave_radiation`, `direct_radiation`, `diffuse_radiation` in `minutely_15`, 288 points for `forecast_days=3`; direct plus diffuse equals shortwave exactly (0 difference at every point). That answer is stored as `tests/fixtures/open_meteo/forecast_three_days.json`. The two-day `forecast.json` keeps its dates and shortwave values (many tests depend on them) and gained direct and diffuse series: direct is the shortwave value times the direct share the live answer had at the same time of day, diffuse the rest; they are synthetic, not recorded.
- CI: HACS step removed, job renamed "hassfest". README, CHANGELOG updated; `CONTEXT.md` needed no change.

Decisions the ticket did not settle:

- A sample counts as curtailed from the moment `curtailing_pv` is seen true until 60 s later; the sample exactly 60 s after counts as clean.
- A known sample after an unknown one is credited the time since the unknown sample (at most 30 s); the unknown sample itself covers nothing.
- `_async_tick` no longer inspects production; it only rolls the quarter-hour.
- In the fit, "both components" means a record that participates (not curtailed, irradiance of at least 20 W/m²) and has both; if the direct coefficient is negative it is set to 0 and diffuse refitted alone (and the reverse); a degenerate hour (zero sums) falls back to the single factor.
- A parts column of another length than the times is an Open-Meteo error; an answer without parts columns still parses (parts `None`), and a null part is `None`, not 0.
- `convert` now takes `IrradiancePoint`s instead of `(start, value)` tuples.

Not done: nothing deliberately left out. The pairs only exist once 20 records with components have accumulated per hour (about a week of days in full sun), so the effect shows after that.
