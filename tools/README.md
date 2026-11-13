# Development tools

Not part of the integration. Plain Python 3 (no Home Assistant, no dependencies).

## Replay: `replay.py`

Replays recorded quarter-hours of one House through the Charging Planner.

    uv run python tools/replay.py data.json [--json] [--timezone Europe/Berlin]
        [--prices-known-at 13:00] [--modes current none price-level-block]

The planner is loaded from `custom_components/nulleinspeisung/charging_planner.py`
by file path, so Home Assistant is not imported.

### Input

    {"house": {"capacity_kwh", "max_charge_power_w", "efficiency" (fraction),
               "charge_target", "floor", "top_up_level" | null,
               "safety_buffer" | null (percent), "forecast_share" (percent),
               "start_charge_level"},
     "quarters": [{"start" (ISO, with offset), "price", "qualifies",
                   "load_w", "pv_w",
                   "forecast_load_w" (default load_w), "forecast_pv_w" (default pv_w),
                   "support_w" (default 0)}]}

`load_w` and `pv_w` are what really happened; the `forecast_*` fields are what the
planner is told. With none, the planner has perfect foresight of the quarters it
may see.

### What it does

At every quarter-hour it builds the planner's view (the quarters from now to the
end of the published prices: the rest of the local day, from 13:00 also the next
one), calls `plan_charging` as `GridCharging._make_plan` does (top-up window,
Safety Buffer, latch, running slot) and applies the result to a simulated battery
with the real load and PV: charging from the grid at the planned energy, held (no
discharge, PV surplus charges), or free (the battery covers the net load down to
the floor, PV surplus charges up to full). Each side of the battery loses the
square root of the efficiency, as in the planner.

### Output

Per local day and in total, for the current rules and two baselines: grid energy
bought and its cost (`bought`, `cost`), of which charging (`charged`), energy
delivered by the battery, energy bought in quarter-hours that do not qualify
(`dear`: the shortfalls actually paid), PV surplus the battery could not take,
and the end charge level.

* `none`: no Grid Charging; the battery is free all the time.
* `price-level-block`: the rule before ticket 37; no Grid Charging, and the
  battery is held in every qualifying quarter-hour.

The energy left in the battery is valued as "stored value": the energy it can
still deliver (content times the square root of the efficiency) at the mean price
of the period. "cost less stored value" is comparable between runs that end at
different levels. The valuation is crude; read it as a hint.

### Simplifications

* The BatteryLife Top-up is wanted whenever `top_up_level` is set and the level
  has not been reached on the local day; a top-up still wanted at midnight is not
  carried over.
* The planner is called once per quarter-hour, at its start; the real integration
  replans more often. A charging slot is applied for its planned grid energy.
* Cost is the import cost; export revenue is ignored. PV is the production as
  recorded: curtailment by the integration hides what the panels could have made.
* No DC Battery model: `support_w` is a given series.
* Fixtures in `tests/fixtures/replay/` are artificial.

## Export: `export_history.py`

Writes the replay input from Home Assistant's history (REST API,
`/api/history/period`).

    export HA_URL=...   # base URL, from the environment only
    export HA_TOKEN=... # long-lived access token, from the environment only
    uv run python tools/export_history.py llm-wiki/private/week.json \
        --start 2026-11-09T00:00:00+01:00 --end 2026-11-16T00:00:00+01:00 \
        --grid sensor.<grid_power> --production sensor.<inverter_1> sensor.<inverter_2> \
        --battery sensor.<battery_power> --price sensor.<price> --level sensor.<level> \
        --qualifying-levels very_cheap cheap \
        --capacity-kwh 10 --max-charge-power-w 2000 --efficiency 0.8

Values are averaged over each quarter-hour, weighted by the time each state held.
`load = grid + production - battery`, with grid power positive for import and
battery power positive while charging. All powers must be in W. Quarter-hours
without price, level or grid value are left out; missing production or battery
power counts as 0. The export has no forecast fields, so the planner gets the
real values.

Real exports describe an installation: put them in `llm-wiki/private/`, which git
ignores, never in the repository. The token is never printed or written.
