# Nulleinspeisung

<img src="custom_components/nulleinspeisung/brand/icon.png" alt="Nulleinspeisung icon" width="96">

A Home Assistant custom integration for zero feed-in. It regulates Hoymiles inverters behind OpenDTU so that each house exchanges a chosen amount of power with the grid, restarts DTUs that stop talking to their inverters, and, for houses with a Victron ESS battery and Tibber prices, charges the battery from the grid when that is worth it. Texts are in English and German.

It is built for one owner's installation (two houses, twelve inverters, three DTUs, Victron MultiPlus-II GX batteries) and runs there. Other setups should work but have not been tried.

Terms in capitals (House, Inverter Limit, Curtailment, Charging Plan, ...) are defined in [CONTEXT.md](CONTEXT.md).

## Status

Version 0.11.0 (see [CHANGELOG.md](CHANGELOG.md)). The control features are in production use on the owner's installation. Everything that sets inverter limits or writes to the battery is switchable per House.

## Requirements

- Home Assistant 2026.9 or later.
- One or more [OpenDTU](https://github.com/tbnobody/OpenDTU) gateways with Hoymiles inverters, reachable over the network, with the administrator password.
- For every House, a Home Assistant power sensor for the net power at the grid connection (any meter).
- For a House with an AC battery:
  - a Victron GX with ESS whose built-in MQTT broker is reachable from Home Assistant without a password,
  - Home Assistant's MQTT integration, connected to the broker that the battery's virtual grid meter reads,
  - if the integration shall supply the grid value to the battery, [mr-manuel's dbus-mqtt-grid](https://github.com/mr-manuel/venus-os_dbus-mqtt-grid) on the GX as the battery's grid meter (Venus OS 3.50 or later for the setpoint override).
- For prices: a [Tibber](https://developer.tibber.com) access token.

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose "Custom repositories".
2. Add `https://github.com/soeren487/nulleinspeisungha` with the category "Integration".
3. Download "Nulleinspeisung" and restart Home Assistant.
4. Add the integration under Settings > Devices & services.

### Manual

Copy the folder `custom_components/nulleinspeisung` into the `custom_components` folder of your Home Assistant configuration, restart Home Assistant, and add the integration under Settings > Devices & services.

Restart Home Assistant after every update.

## Setup

Add the integration once. DTUs and Houses are added as sub-entries; everything can be changed later without removing them.

1. **DTUs.** "Add DTU": the address of the OpenDTU and its administrator password (user `admin`). Optional per DTU: the sun angle above which inverters must deliver data (default 5 degrees), the staleness time (default 120 s) and the wait after a restart (default 180 s). Each DTU needs its own DTU serial in OpenDTU (Settings > DTU Settings). Add as many DTUs as you have; all their inverters appear as devices.
2. **House.** "Add House": a name, the location, the Grid Meter sensor, whether that sensor reports import or export as positive, and optionally the Tibber home.
3. **Inverters.** Choose the inverters of any DTU that belong to the House; an inverter belongs to at most one House, and unassigned inverters are shown but never controlled.
4. **Battery-backed Inverters.** Mark the inverters that are fed by a DC battery (their input is a battery, so power they may not deliver stays stored). All others are PV Inverters. For each battery-backed inverter you may name the sensors that report the energy stored in its DC batteries (Wh or kWh); the plan then counts on what they will deliver at night.
5. **AC Battery.** Optional. The address and port of the Victron GX, the usable capacity in kWh and the MQTT topic that the battery's virtual grid meter listens to. Leave the address empty for a House without an AC battery.
6. **Tibber.** In the options of the integration (Configure): the Tibber token, and optionally a notification target that also receives repair issues. Then choose the Tibber home in the House's settings.

A new House starts regulating its inverters at once, because "Regulate feed-in" is on by default; switch it off first if you want to set the parameters beforehand. "Charge from grid" is off by default, because it spends money.

## What it does

### Curtailment

Per House the integration sets the Inverter Limits so that the Grid Power stays at the Feed-in Setpoint (default 0 W; positive exports, negative imports, which is useful as a safety margin) within a tolerance band (default 30 W). Surplus goes first to the house load, then to charging the AC battery, then to export up to the setpoint; only beyond that are limits lowered. Battery-backed inverters are lowered first and raised last, and are held to what the House consumes so that their DC batteries neither export nor charge the AC battery. All PV Inverters of a House get the same percentage, not below a floor (default 5 %).

An AC battery regulates the meter within seconds, so the House does not trust single readings: it regulates on the mean of the last minute and lowers only for export beyond the band that lasts for two runs. While inverters are held back and the House is not exporting, it probes upward by a few percent and takes the step back if export persists. Limits are relative and non-persistent and are sent only when they change and the inverter is reachable. If the Grid Meter or all DTUs fail, the House holds its last limits (or sets all inverters to 100 %, as chosen) and raises a repair issue.

### Stuck DTU restart

A DTU is stuck when the sun is above the configured angle and none of its PV inverters has delivered fresh data for the staleness time, or when a PV inverter on another DTU of the same House produces at least 50 W while this DTU has none. It is then restarted through its API. After a restart the House waits (default 3 minutes); after three failed restarts in a row the wait doubles each time up to one hour and a repair issue is raised. Battery-backed inverters are ignored in this judgement. A DTU is never restarted merely because it is night.

### Reported Grid Power

With "Send grid power to Victron" on, the integration publishes the true Grid Power on every meter update to the topic of the battery's virtual grid meter, replacing any other publisher (for example Node-RED). If the Grid Meter fails or Home Assistant stops, publishing stops and the grid meter driver takes the meter away from the battery after its timeout.

### Grid Charging

Needs an AC battery, prices and a usable capacity, and acts only where "Send grid power to Victron" is on. The Charging Plan covers every quarter-hour up to the end of the known prices (at most 48 hours) and follows the battery's content. In quarter-hours that do not qualify by Price Level (default: cheap and very cheap qualify) the battery carries the Expected Load minus the counted share of the PV Forecast and the DC Battery Support, down to its minimum charge level. What it cannot carry is a shortfall at that quarter-hour's price. Each shortfall, earliest first, is bought beforehand in the cheapest qualifying quarter-hours before it, within the Maximum Charge Power and the Charge Target.

A charging quarter-hour charges at the Maximum Charge Power (or the Safety Buffer's power) for as long as the plan keeps it. PV surplus inside a charging quarter-hour is not counted by the plan.

**Efficiency test.** A battery returns only part of what is charged (default round trip 78 %). A quarter-hour is used only if its price is at most the efficiency times the price of the shortfall it covers, and the energy bought is larger than the energy stored by the square root of the efficiency. If quarter-hours qualify by Price Level but none passes, nothing is charged and "Grid charging blocked by efficiency" shows it; a switch skips the test.

Charging uses Victron's volatile setpoint override, rewritten every cycle and released when not charging. Switching Grid Charging off, losing prices or the battery, or an active Dynamic ESS releases it at once.

### Discharge Block on demand

The battery is held (maximum discharge power 0 W; PV surplus can still charge it) in a quarter-hour only when the plan shows that its energy is needed later at a higher price and would be missing there. The latest cheap quarter-hours are held first and only as many as needed; what the plan buys is held until the dearer quarter-hour. A cheap Price Level alone is no reason: Tibber's level compares with the days before, not with an absolute price, so it can stay "cheap" for more than a day. In a long cheap period with nothing dearer waiting, the battery discharges as usual.

### BatteryLife Top-up

With the Victron's ESS mode "Optimized (with BatteryLife)" the minimum charge level rises on days without a full charge and falls only on a day the battery reaches 100 %. While the minimum the Victron applies is above the configured one and the battery has not reached the top-up level on the current local day, the plan charges up to that level (default 100 %, adjustable from 90 to 100 %) in the cheapest qualifying quarter-hours of the rest of the day, without the efficiency test, within the Maximum Charge Power and the Charge Target. The window ends at midnight, or at the end of a run of qualifying quarter-hours that is going on at midnight. The battery is held from the first top-up quarter-hour until the level is reached. A battery reports 100 % only at the end of absorption, so the last part is slow; the top-up stays wanted until the level is reported. A switch turns it off.

### Safety Buffer

The plan counts on the PV Forecast, but a cloudy day would leave few cheap quarter-hours. In every run the House also plans two pessimistic cases at the Maximum Charge Power reduced by the Safety Buffer (default 50 %), qualifying quarter-hours latest first: the top-up with no PV at all, and the top-up and shortfalls with the counted forecast. If either needs the current quarter-hour, the House charges now, steadily at the power those cases ask for (at least 500 W, at most the Maximum Charge Power), and keeps going in the following quarter-hours while they still need energy.

### PV Forecast

Per House from Open-Meteo irradiance at the House's location, converted to power by a model learned per hour of the day from the inverters' own production history, leaving out intervals in which the House was curtailing. It is usable after 14 days of history. Only a share of it is counted (default 70 %). Until it is usable, or with "Use PV forecast" off, no PV is counted and the plan is pessimistic about PV.

### Expected Load

The power the House is expected to consume per quarter-hour, a weighted average of the same quarter-hour on past days (weights halving every 7 days), computed from Grid Power, inverter production and battery power. It is learned after 7 days; until then a fallback daily consumption (default 10 kWh) stands in.

### DC Battery Support

For battery-backed inverters with named DC battery sensors, the House learns their usual output per quarter-hour from their history and expects it until the stored energy, reduced by an assumed conversion loss of 15 %, is used up. The AC battery then has to deliver that much less until sunrise. Without sensors, or when a sensor gives no value, no support is counted. A few nights of history are needed first.

## Entities

Entities are grouped on devices: each DTU and each Inverter has a device, and each House has up to three devices: the House, "&lt;House&gt; Inverter control" ("Wechselrichter-Regelung") and "&lt;House&gt; Grid charging" ("Netzladen"). Names are taken from the translation files. Entities for features a House does not have (no battery, no prices, ...) are not created. Diagnostic and configuration entities are marked as such in Home Assistant.

### DTU

| English | German | Type |
| --- | --- | --- |
| Stuck | Hängt | binary sensor |
| Restart | Neustart | button |
| Automatic restart | Automatischer Neustart | switch |
| Restarts | Neustarts | sensor |
| Last restart | Letzter Neustart | sensor |

### Inverter

| English | German | Type |
| --- | --- | --- |
| Reachable | Erreichbar | binary sensor |
| Producing | Produziert | binary sensor |
| Power | Leistung | sensor |
| Inverter limit | Wechselrichter-Limit | sensor |
| Data age | Alter der Daten | sensor |
| Rated power | Nennleistung | sensor |

### House

| English | German | Type |
| --- | --- | --- |
| Grid Power | Netzleistung | sensor |
| Inverter production | Erzeugung der Wechselrichter | sensor |
| PV Inverter production | Erzeugung der PV-Wechselrichter | sensor |
| Battery-backed Inverter production | Erzeugung der batteriegestützten Wechselrichter | sensor |
| Consumption | Verbrauch | sensor |
| Assigned Inverters | Zugeordnete Wechselrichter | sensor |
| Expected Load | Erwartete Last | sensor |
| Expected Load next 24 hours | Erwartete Last nächste 24 Stunden | sensor |
| Load history days | Lastverlaufstage | sensor |
| Expected Load learned | Erwartete Last gelernt | binary sensor |
| Fallback daily consumption | Ersatz-Tagesverbrauch | number |

With an AC Battery:

| English | German | Type |
| --- | --- | --- |
| AC Battery connected | AC-Batterie verbunden | binary sensor |
| AC Battery charge level | Ladestand der AC-Batterie | sensor |
| AC battery minimum charge level | Minimaler Ladestand der AC-Batterie | sensor |
| AC Battery power | Leistung der AC-Batterie | sensor |
| AC Battery headroom | Spielraum der AC-Batterie | sensor |
| Maximum Charge Power | Maximale Ladeleistung | number |
| Reported Grid Power | Gemeldete Netzleistung | sensor |
| Send grid power to Victron | Netzleistung an Victron senden | switch |

With Tibber prices:

| English | German | Type |
| --- | --- | --- |
| Price | Preis | sensor |
| Price Level | Preisniveau | sensor |
| Prices known until | Preise bekannt bis | sensor |

With the PV Forecast:

| English | German | Type |
| --- | --- | --- |
| PV Forecast power | PV-Prognose Leistung | sensor |
| PV Forecast today | PV-Prognose heute | sensor |
| PV Forecast tomorrow | PV-Prognose morgen | sensor |
| PV Forecast history days | PV-Prognose Verlaufstage | sensor |
| PV Forecast usable | PV-Prognose verwendbar | binary sensor |

### Inverter control

| English | German | Type |
| --- | --- | --- |
| Regulate feed-in | Einspeisung regeln | switch |
| Feed-in Setpoint | Einspeise-Sollwert | number |
| Update Interval | Aktualisierungsintervall | number |
| Tolerance band | Toleranzband | number |
| Limit floor | Untergrenze des Limits | number |
| Limit slew rate | Änderungsrate des Limits | number |
| Response reserve | Reaktionsreserve | number |
| On failure | Bei Störung | select (Hold last limits / Set all to 100 %) |
| Control state | Regelzustand | sensor (off, holding, lowering, raising, probing, no Grid Power, no Inverter, failure) |
| Inverter Limit | Wechselrichter-Limit | sensor |
| Battery-backed Inverter Limit | Limit der batteriegestützten Wechselrichter | sensor (only with battery-backed inverters) |

### Grid charging

Settings:

| English | German | Type |
| --- | --- | --- |
| Charge from grid | Aus dem Netz laden | switch |
| Charge at price level | Laden bei Preisniveau | select (Very cheap only / Cheap and very cheap / Normal and cheaper) |
| Charge target | Ladeziel | number |
| AC battery efficiency | Wirkungsgrad der AC-Batterie | number |
| Ignore efficiency check | Wirkungsgrad-Prüfung übergehen | switch |
| Use PV forecast | PV-Prognose berücksichtigen | switch |
| Counted share of PV forecast | Angerechneter Anteil der PV-Prognose | number |
| Safety buffer | Sicherheitspuffer | number |
| BatteryLife top-up | BatteryLife-Vollladung | switch |
| BatteryLife top-up level | Ziel der BatteryLife-Vollladung | number |

What the plan does:

| English | German | Type |
| --- | --- | --- |
| Grid charging state | Netzladen | sensor |
| Grid charging reason | Begründung Netzladen | sensor |
| Next charging start | Nächster Ladebeginn | sensor |
| Discharge block active | Entladesperre aktiv | binary sensor |
| Safety buffer active | Sicherheitspuffer greift | binary sensor |
| Safety buffer charge power | Ladeleistung Sicherheitspuffer | sensor |
| PV forecast in use | PV-Prognose wird verwendet | binary sensor |
| Grid charging blocked by efficiency | Netzladen durch Wirkungsgrad blockiert | binary sensor |

The figures behind it:

| English | German | Type |
| --- | --- | --- |
| Energy to buy | Zu kaufende Energie | sensor |
| Energy missing | Fehlende Energie | sensor |
| Shortfall in expensive periods | Engpass in teuren Zeiten | sensor |
| Uncovered shortfall | Nicht gedeckter Engpass | sensor |
| Energy for BatteryLife top-up | Energie für BatteryLife-Vollladung | sensor |
| Reference price | Referenzpreis | sensor |
| Battery at sunrise | Batterie bei Sonnenaufgang | sensor |
| Forecast surplus | PV-Überschuss laut Prognose | sensor |
| Energy in DC batteries | Energie in den DC-Batterien | sensor (only with named DC battery sensors) |
| Expected DC battery support until sunrise | Erwartete Unterstützung der DC-Batterien bis Sonnenaufgang | sensor (only with named DC battery sensors) |

## Good to know

- **Dynamic ESS must be off** on the GX. The integration raises a repair issue if it is on and does not charge or hold while it is; it never switches it off.
- **Settings live in entities.** A setting whose entity you disable falls back to its default.
- **Limits are non-persistent.** The integration never writes the persistent limit of an inverter; limits return to 100 % at the Inverter's next power-up. Switching "Regulate feed-in" off gives all inverters of the House 100 % once.
- **A negative Feed-in Setpoint is a safety margin.** The control never aims for more import than the AC battery's own stored grid setpoint; if the setpoint asks for more, the battery's value is used and a repair issue is raised. The stored grid setpoint is never written.
- **Why does the House charge or hold?** The sensor "Grid charging reason" gives a state and, in its attributes `text` and `text_de`, one sentence with the figures behind it. "Next charging start" lists the whole plan in its attribute `plan` and the held quarter-hours in `held`; these attributes are not recorded.
- **Diagnostics.** Settings, the inputs of the last plan, the plan and its reason, the battery state and the DTU states can be downloaded from the integration's page ("Download diagnostics"); addresses, passwords, tokens, portal ids, serials, coordinates and topics are redacted.
- **Repair issues** are raised for unreachable or non-helping DTUs, missing prices or a rejected Tibber token, a failed Grid Meter or all DTUs of a House, a battery that does not answer, Dynamic ESS, a Feed-in Setpoint in conflict with the battery's setpoint, a missing MQTT integration and another system publishing on the grid meter topic.
- **When the integration is unloaded or reloaded,** the control loops stop and send nothing further; inverters keep their last limits, except when the entry is disabled or removed: then the inverters the House had limited get 100 % (best effort). When Home Assistant stops, the inverters keep their limits (a restart must not export) unless the House's failure behaviour is set to 100 %, in which case they get 100 %; the setpoint and discharge overrides that the integration set are also released on the GX (best effort), and without the Reported Grid Power the battery's grid meter times out after its driver's timeout.
- **Inverters with 2020 firmware follow a limit slowly** (about 0.5 % of rated power per second). A response reserve of about 10 % makes Curtailment act in seconds on them; newer firmware needs none.
- **The plan reaches only as far as the known prices.** Before tomorrow's prices are published (around 13:00) an expensive period tomorrow is not yet planned for.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```
uv run pytest                                          # tests
uv run ruff check . && uv run ruff format --check .    # lint and format check
```

CI (GitHub Actions) runs the tests and linters, the tests against the oldest supported Home Assistant, a weekly run, and hassfest and HACS validation.

The scripts in [tools/](tools/README.md) are not part of the integration:

- `tools/replay.py` replays recorded quarter-hours of one House through the Charging Planner and compares cost with and without Grid Charging.
- `tools/export_history.py` writes such a file from Home Assistant's history over its REST API (token from the environment; exports describe an installation and must not be committed).

Project knowledge is kept in [llm-wiki/wiki/](llm-wiki/wiki/index.md), the glossary in [CONTEXT.md](CONTEXT.md), decisions in [docs/adr/](docs/adr/), the spec and tickets in `.scratch/nulleinspeisung/`.
