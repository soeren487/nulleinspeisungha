---
type: Reference
title: Requirements decisions
description: All decisions settled in the requirements interview of 2026-10-04, and the facts still to be verified.
tags: [project, requirements]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-05T11:30:00Z }
sources:
  - id: interview
    resource: requirements interview with Soeren, 2026-10-04
    title: Requirements interview
    author: human:soeren
---

Terms are defined in [CONTEXT.md](../../../CONTEXT.md). All decisions below come from the interview.[^interview]

# Settled

| Topic | Decision |
| --- | --- |
| Deliverable | Custom integration via HACS; see [ADR 0001](../../../docs/adr/0001-custom-integration-not-supervisor-app.md) |
| House | One grid connection: exactly one grid meter, any number of inverters, zero or one AC battery, zero or one Tibber home. Number of houses is open-ended |
| Feed-in setpoint | Signed and freely configurable per house: positive exports, negative imports. Regulated with a small configurable tolerance band; short overshoots on load drops are accepted |
| Zero feed-in | A preference, not a legal requirement |
| On failure | Hold the last inverter limits and raise an alert. Per-house option to set all inverters to 100 % instead |
| Persistent inverter limit | Left at 100 %; the integration never writes it |
| Surplus priority | House load, then AC battery charging, then export up to the setpoint, then curtailment |
| Maximum charge power | Configurable per house (about 2100 W today) |
| Curtailment order | Battery-backed inverters first, PV inverters second |
| Battery-backed inverters at night | Held to the house's load, so they neither export nor charge the AC battery. The DC battery manages its own release window |
| Grid meter source | An existing Home Assistant power sensor chosen per house (the Shelly Pro 3EMs are already integrated) |
| Reported grid power | The integration becomes the only publisher to the AC battery's virtual grid meter; Node-RED is switched off once it runs |
| Grid charging | Designed afresh from requirements; the Node-RED flow is not a blueprint |
| Stuck DTU | Web UI and API stay reachable, only radio fails. Restart through the DTU's authenticated API |
| Update interval | Per house, 5 to 60 s, default 15 s |
| Houses | Two today: the living house and the office and workshop house |
| Battery-backed inverters | Two: "Büro 3" and "Haus 3", one per house, each fed by two Anker Solix E1600. They deliver a fixed 300 W from 21:00 to 06:00 as night support; the AC battery covers the rest of the load at any time |
| DTU settings | The integration does not change inverter settings in OpenDTU (no toggling of polling). It never sends limit commands to an unreachable inverter |
| Limit split | The same percentage for all PV inverters of a house, with a configurable floor (default 5 %). Battery-backed inverters are a separate group, curtailed first. Individual percentages per inverter may come later, so the split must be replaceable |
| Stuck DTU detection | By day, when production must be possible but a DTU's inverters are unreachable or deliver no fresh data, restart it after 2 to 3 minutes. Hangs also begin at night and show when the inverters wake up. It happens up to several times a day |
| Grid charging slots | Charge at maximum charge power in the cheapest quarter-hours before the deadline, restricted to configurable price levels (default cheap and very cheap). Replanned when prices arrive and as the charge level changes. Needs usable battery capacity per house |
| Grid charging switch | Can be switched off manually per house |
| Grid charging target and deadline | Target charge level configurable per house, default 100 %. Deadline is sunrise |
| Discharge block | While grid charging is switched on, the AC battery is not discharged during any quarter-hour whose price level is cheap or very cheap |
| PV forecast | Wanted in the first version, with the location configurable per house, so that a sunny next day reduces grid charging |
| Virtual grid meter | mr-manuel `dbus-mqtt-grid` on Venus OS v3.67, so Victron's setpoint override (3.50 or later) is available |
| Forecast rule | From sunrise to the next cheap period, sum expected load minus a cautious PV estimate per quarter-hour; the peak of that running sum is the energy the battery must hold at sunrise. Grid charging buys up to that, capped by the target charge level. Only 70 % of forecast PV is counted (configurable) |
| Expected load | Learned per quarter-hour as a weighted average of past days, from grid power, inverter production and battery power. A configured value per house is the fallback until a week of history exists |
| Forecast source and inputs | Open-Meteo, called by the integration. Per house only latitude and longitude; the irradiance-to-power conversion is learned from production history, excluding curtailed intervals. Tilt and direction per inverter are an optional refinement. Until about two weeks of history exist the forecast is not used and grid charging fills to the target |
| Discharge block scope | Applies in every quarter-hour whose price level is in the configured set, also when no charging is planned. PV surplus may still charge the battery; battery-backed inverters still deliver. The set is the same one that qualifies for charging |
| Grid charging mechanism | The true grid power is always passed to the AC battery. Charging uses Victron's setpoint override, the discharge block uses a maximum discharge power of 0. Conditional on a test on the real system (override expiry, frozen meter value); fallback is the offset on the reported grid power |
| Dynamic ESS | Must be off on both GX devices. The integration checks and raises a repair issue, but does not switch it off |
| Stuck DTU rule | Stuck when the sun is above a configurable angle (default 5 degrees) and none of the DTU's PV inverters has fresh data for 2 minutes; battery-backed inverters are ignored. Also stuck at any sun angle when a PV inverter on another DTU of the same house is producing at least 50 W with fresh data while this DTU has no fresh data (narrowed in ticket 05: a battery-backed inverter producing at night must not count, and roofs wake at different times). Otherwise never restarted at night |
| Restart policy | Wait 3 minutes after a restart. After 3 failed restarts in a row, double the wait each time up to 1 hour and alert. Fresh data resets this. No daily cap |
| Entities | Per house: switches for curtailment and grid charging; numbers for feed-in setpoint, update interval, target charge level, maximum charge power; sensors for inverter limit, control state, planned charging quarter-hours, forecast surplus, energy to buy. Per DTU: stuck indicator, restart button, restart counter, last restart time. Per inverter: limit and reachability |
| Alerts | Home Assistant repair issues, optionally also a chosen notification target |
| Tibber | One account and token for both houses; each house selects its Tibber home |
| MQTT broker | External to Home Assistant, reachable from it |
| Delivery order | 1. DTUs, inverter assignment, stuck detection and restart. 2. Curtailment, reading the AC battery. 3. Reported grid power, grid charging, discharge block; Node-RED off. 4. PV forecast |
| Negative feed-in setpoint | A safety margin against accidental export. The integration reads the Victron's grid setpoint and never curtails to more import than that; if the feed-in setpoint asks for more, it uses the Victron's value and raises a repair issue. It never writes the Victron's stored grid setpoint. A positive setpoint needs no alignment |
| Victron access | An own MQTT connection to each GX's built-in broker; the owner enters the GX address per house and the portal id is discovered. The reported grid power goes through Home Assistant's MQTT integration to the external broker ([ADR 0002](../../../docs/adr/0002-direct-mqtt-connection-to-each-gx.md)) |
| Testing | Automated tests against simulated DTUs, meters and GX devices. Only the production Home Assistant exists. Real system: reading at any time; writes only in sessions Soeren approves each time, starting with one inverter he names. The two Victron behaviour tests are done together with him before stage 3 is built |
| Name and languages | Domain `nulleinspeisung`, displayed as "Nulleinspeisung", English and German texts |
| Distribution | Development on the self-hosted forge; mirrored to a GitHub repository Soeren creates, added to HACS as a custom repository. Until then installed by copying into `custom_components` |
| Tolerance band | ±30 W around the feed-in setpoint, configurable per house |
| Curtailment switched off | All inverters of the house return to 100 % |
| Grid meter failure | Sensor unavailable or unchanged for 3 update intervals. The failure behaviour applies and the AC battery stops receiving a grid value, which makes it idle within 60 s |
| No prices available | No grid charging, no discharge block, a repair issue |
| Grid charging switched off | Override released and discharge block lifted immediately |
| After a DTU restart | Current limits are re-sent once the inverters are reachable |
| Unassigned inverters | Shown, never controlled |
| Battery capacity | Usable kWh entered per house; charge level read from the Victron |
| Sunrise | Computed from the house's location, the same one used for the forecast |

# To verify

No decisions are open. These facts still have to be established on the real system:

- Victron access path: settled on 2026-10-05. The external broker carries only the virtual grid meter's feed and custom read-only battery topics. Each GX device's built-in broker is reachable on the network without a password and publishes everything the design needs, including volatile overrides for setpoint, maximum discharge power and maximum charge power. The integration therefore connects to each GX directly (see ADR 0002) and publishes the reported grid power through Home Assistant's MQTT integration to the external broker, where the grid meter driver reads it. Both systems have Dynamic ESS off and a stored grid setpoint of 0 W.
- Victron overrides: tested on 2026-10-06. The setpoint override and the discharge override are honoured and do not expire, so grid charging and the discharge block use them as designed. Still open: what ESS does when the grid value freezes or stops, to be observed when the integration takes over publishing it.
- The time from a limit command's acknowledgement to changed inverter output.
- The sign of each house's grid meter sensor.
- Which of the Garage DTU's HM-600 inverters belongs to which house.

[^interview]: Requirements interview
