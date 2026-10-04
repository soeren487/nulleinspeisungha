# Spec: Nulleinspeisung integration

Status: ready-for-agent

Terms in capitals are defined in `CONTEXT.md`. Background research and the decision log are in `llm-wiki/wiki/` (start at `index.md`). ADR 0001 explains why this is a custom integration.

## Problem Statement

I run two Houses from one Home Assistant, each with its own grid connection, Inverters, AC Battery and dynamic Tibber tariff. Today nothing keeps a House's export at the level I want, my twelve Inverters are reached through three DTUs that lose radio contact several times a day until someone restarts them, and the logic that charges the AC Batteries from the grid when electricity is cheap lives in a Node-RED flow that works by falsifying the grid measurement the batteries see. I want one place in Home Assistant that does all of this properly, per House.

## Solution

A Home Assistant custom integration called "Nulleinspeisung". DTUs are connected once; each Inverter is then assigned to a House. Per House the integration regulates the Inverter Limits so that Grid Power stays at the Feed-in Setpoint, giving the AC Battery priority over export and export priority over Curtailment. It restarts a Stuck DTU by itself. It hands the true Grid Power to the AC Battery, charges the battery from the grid in the cheapest low-priced quarter-hours before sunrise, keeps it from discharging while prices are low, and buys less when the next day's sun will fill the battery anyway.

## User Stories

### Setup

1. As the owner, I want to add a DTU by its address and admin password, so that its Inverters become available without tying the DTU to a House.
2. As the owner, I want to add any number of DTUs, so that all twelve Inverters are reachable.
3. As the owner, I want to see every Inverter of a DTU with its name, model, rated power, reachability and current Inverter Limit, so that I can check the connection worked.
4. As the owner, I want to create any number of Houses, so that both grid connections are handled by one Home Assistant.
5. As the owner, I want to pick an existing power sensor as a House's Grid Meter, so that the integration works with the Shelly devices I already have and with any other meter.
6. As the owner, I want to state whether that sensor reports import or export as positive, so that a wrongly signed sensor does not invert the control.
7. As the owner, I want to assign Inverters from any DTU to a House, so that a DTU serving both Houses is handled correctly.
8. As the owner, I want an Inverter to belong to at most one House, so that two Houses never fight over it.
9. As the owner, I want to mark an Inverter as a Battery-backed Inverter, so that it is treated differently from a PV Inverter.
10. As the owner, I want unassigned Inverters to be shown but never controlled, so that I can leave some out deliberately.
11. As the owner, I want a House to work without an AC Battery and without a Tibber home, so that the integration is not limited to my exact setup.
12. As the owner, I want all texts in English and German, so that I can use it in my language.
13. As the owner, I want to change every setting later without removing the House or DTU, so that tuning does not mean reconfiguring.

### Stuck DTU

14. As the owner, I want a DTU that has stopped exchanging data with its PV Inverters during the day to be restarted automatically within about two minutes, so that I do not lose production control.
15. As the owner, I want a hang that began at night to be caught as soon as production must be possible, so that the morning is not lost.
16. As the owner, I want a DTU never to be restarted merely because it is night, so that restarts are not wasted.
17. As the owner, I want Battery-backed Inverters ignored when judging whether a DTU is stuck, so that their normal daytime silence does not trigger restarts.
18. As the owner, I want a DTU treated as stuck when another DTU of the same House has producing Inverters while this one has no fresh data, so that hangs at low sun are caught too.
19. As the owner, I want a single unreachable Inverter among reachable ones not to count as a Stuck DTU, so that one bad radio link does not restart everything.
20. As the owner, I want repeated unsuccessful restarts to slow down and alert me, so that snow or a real fault does not cause endless restarts.
21. As the owner, I want a restart button, a stuck indicator, a restart counter and the time of the last restart per DTU, so that I can see and trigger this myself.
22. As the owner, I want the sun angle, the staleness time and the waiting time after a restart to be configurable per DTU, so that I can tune detection.
23. As the owner, I want the current Inverter Limits re-sent after a DTU restart, so that a restart does not leave Inverters at a stale limit.

### Curtailment

24. As the owner, I want to set a signed Feed-in Setpoint per House, so that I decide how much the House exports or imports.
25. As the owner, I want export beyond the Feed-in Setpoint to be removed by lowering Inverter Limits, so that I do not give energy away.
26. As the owner, I want Inverter Limits raised again as soon as the House can use more power, so that no production is wasted.
27. As the owner, I want a tolerance band around the Feed-in Setpoint, so that the Inverters are not readjusted for every small fluctuation.
28. As the owner, I want to set the Update Interval per House between 5 and 60 seconds, so that I can trade responsiveness against radio traffic.
29. As the owner, I want the AC Battery to charge from surplus before anything is curtailed, so that PV energy is stored, not discarded.
30. As the owner, I want to set the Maximum Charge Power per House, so that the integration knows when the battery cannot absorb more.
31. As the owner, I want Battery-backed Inverters curtailed before PV Inverters, so that the energy held back stays in the DC Battery.
32. As the owner, I want Battery-backed Inverters never to deliver more than the House is consuming, so that the DC Battery neither exports nor charges the AC Battery.
33. As the owner, I want all PV Inverters of a House to receive the same percentage, with a floor below which the integration does not go, so that the behaviour is simple and Inverters are not driven into unstable low limits.
34. As the owner, I want limit commands never sent to an unreachable Inverter, so that the DTU's radio is not blocked.
35. As the owner, I want a negative Feed-in Setpoint to work as a safety margin without making the AC Battery discharge against it, so that I do not burn stored energy while curtailing.
36. As the owner, I want to switch Curtailment off per House and have all its Inverters return to 100 %, so that I can hand control back at any time.
37. As the owner, I want the persistent limit stored in the Inverters never to be written, so that Inverters start unthrottled.
38. As the owner, I want the integration to hold the last Inverter Limits and alert me when the Grid Meter or a DTU fails, so that a fault does not cause erratic behaviour.
39. As the owner, I want a per-House option to set all Inverters to 100 % on failure instead, so that I can prefer production over the setpoint.
40. As the owner, I want to see per House the control state and the current Inverter Limit, so that I understand what the integration is doing.

### AC battery and grid charging

41. As the owner, I want the integration to hand the true Grid Power to the AC Battery, so that Node-RED is no longer needed and the battery's own statistics are correct.
42. As the owner, I want the AC Battery to stop receiving a value when the Grid Meter fails, so that it goes idle instead of acting on a stale value.
43. As the owner, I want to enter one Tibber token and choose a Tibber home per House, so that each House uses its own prices.
44. As the owner, I want Grid Charging to run in the cheapest quarter-hours before sunrise, so that the missing energy is bought at the lowest price.
45. As the owner, I want to choose which Price Levels qualify, defaulting to cheap and very cheap, so that I never charge at normal or high prices.
46. As the owner, I want a Charge Target per House, defaulting to 100 %, so that I decide how full the battery should be by sunrise.
47. As the owner, I want the Charging Plan to cover the whole night across midnight, so that cheap hours after midnight are used.
48. As the owner, I want the Charging Plan recomputed when new prices arrive and as the charge level changes, so that it stays correct.
49. As the owner, I want to see the planned charging quarter-hours and the energy to be bought, so that I can check the plan.
50. As the owner, I want a Discharge Block in every quarter-hour whose Price Level qualifies, so that stored energy is kept for expensive hours.
51. As the owner, I want PV surplus still to charge the battery during a Discharge Block, so that free energy is never refused.
52. As the owner, I want to switch Grid Charging off per House and have charging and the Discharge Block end immediately, so that I can override it.
53. As the owner, I want no Grid Charging and no Discharge Block when prices are unavailable, and an alert, so that the battery is not steered blind.
54. As the owner, I want to be warned if Victron's Dynamic ESS is active, so that two schedulers do not fight.
55. As the owner, I want to enter the usable battery capacity per House, so that the missing energy can be calculated.

### Forecast

56. As the owner, I want to enter a location per House, so that sunrise and the PV Forecast are computed for the right place.
57. As the owner, I want Grid Charging to buy only what the battery must hold at sunrise to bridge the time until the sun covers the House, so that I do not pay for energy the sun delivers for free.
58. As the owner, I want the PV Forecast derived from an irradiance forecast and my Inverters' own production history, so that I do not have to describe every panel.
59. As the owner, I want Curtailment periods excluded from that history, so that throttled production does not distort the forecast.
60. As the owner, I want the Expected Load learned from the House's own past, so that the plan fits how the House is used.
61. As the owner, I want only a configurable share of the PV Forecast counted, defaulting to 70 %, so that a wrong forecast does not leave the battery empty.
62. As the owner, I want Grid Charging to fill to the Charge Target while too little history exists, so that the feature is safe from day one.
63. As the owner, I want to see the forecast and the resulting energy to buy, so that I can judge whether the forecast is trustworthy.

### Alerts

64. As the owner, I want problems shown as Home Assistant repair issues, so that I find them where I look for problems.
65. As the owner, I want to optionally name a notification target, so that I am told on my phone as well.

## Implementation Decisions

### Shape

- A custom integration, domain `nulleinspeisung`, asynchronous Python, installable through HACS as a custom repository (ADR 0001).
- One config entry for the integration. DTUs and Houses are subentries of it. The Tibber token and the notification target belong to the entry; everything else belongs to a DTU or a House.
- A House holds: name, location, Grid Meter sensor and its sign, assigned Inverters with their Battery-backed flag, and optionally the AC Battery's portal ID, usable capacity, Maximum Charge Power and Tibber home.
- Runtime values the owner changes often are entities, not options: Feed-in Setpoint, Update Interval, Charge Target, Maximum Charge Power as numbers; Curtailment and Grid Charging as switches.

### Modules

Each module hides one external system or one decision behind a small interface, so that decisions can be tested without devices.

- **DTU client.** Talks to one OpenDTU over its REST API: read all Inverters with data age, reachability, production and limit status; set a non-persistent relative limit; restart the DTU. Writes use the admin password; reads need none. It never writes a persistent limit and never changes DTU or Inverter settings.
- **DTU health.** A pure decision: from the Inverter observations of one DTU, the sun's elevation, and whether another DTU of the same House is producing, decide whether the DTU is stuck; and from the restart history decide whether a restart is due. Time is passed in.
- **Curtailment controller.** A pure decision per House and Update Interval: from Grid Power, Feed-in Setpoint, tolerance band, AC Battery state and Inverter states, produce the allowed production for the Battery-backed group and for the PV group.
- **Limit split.** Turns a group's allowed production into an Inverter Limit per Inverter. The first strategy gives every Inverter of the group the same percentage, not below the floor. It is a replaceable strategy, because individual percentages are planned.
- **AC battery gateway.** Talks to one Victron GX over MQTT: reads charge level, battery power, the stored grid setpoint and the Dynamic ESS mode; writes the setpoint override and the maximum discharge power; publishes the Reported Grid Power to the virtual grid meter; keeps the GX's MQTT values alive.
- **Price source.** Fetches quarter-hour prices with Price Level for a Tibber home, today and tomorrow.
- **Charging planner.** A pure decision: from prices, charge level, capacity, Maximum Charge Power, qualifying Price Levels, sunrise and the energy needed at sunrise, produce the Charging Plan and the Discharge Block periods.
- **Forecast.** Fetches irradiance for a location from Open-Meteo; a learned model converts it to a PV Forecast; a second learned model gives the Expected Load; a pure calculation turns both into the energy needed at sunrise.
- **House coordinator.** Runs the Update Interval, gathers inputs, calls the decisions, sends the results, and owns the House's entities and failure state.

### Rules

- **Sign convention.** Internally, Grid Power is positive for import. The Feed-in Setpoint is positive for export. The control target for Grid Power is therefore the negated setpoint.
- **Surplus priority.** House load, then AC Battery charging, then export up to the Feed-in Setpoint, then Curtailment. While the AC Battery can still absorb more (below full and charging below its Maximum Charge Power), that headroom counts as demand: PV Inverters are not curtailed below what the battery could take.
- **Battery headroom.** What the AC Battery can still take is the smaller of the Maximum Charge Power and what its BMS allows, minus its current charge power and a 100 W margin; zero when it is full or its state is stale. The headroom is added to the deviation unless the House exports beyond the band. If the House exports beyond the band while headroom is computed, the first run waits (the battery ramps up within seconds); a second consecutive run lowers the limits and ignores the headroom for 5 minutes.
- **Curtailment step.** Inside the tolerance band nothing changes. Outside it, the allowed production changes by the deviation. Lowering takes from the Battery-backed group first; raising gives to the PV group first.
- **Starting point of a step.** Raising starts from the allowance the House last gave. Lowering starts, per Inverter, from the smaller of that allowance and its actual production, because a limit only bites below what is really produced. A production reading counts only when it is newer than the House's last limit change for that Inverter; otherwise the allowance is used.
- **Own limits only.** The House never trusts a limit it did not send itself: an Inverter it has not sent one to, or that was not controllable in the last run, is taken to be at 100 %. A restarted DTU reports 0 % for every Inverter, and an Inverter that was off has lost its non-persistent limit.
- **Battery-backed cap.** The Battery-backed group is never allowed more than the House's current consumption, computed from Grid Power, Inverter production and AC Battery power. Battery headroom does not count for this group.
- **Regulating on the meter.** The loop corrects on Grid Power, which is fresh. Inverter readings can be 20 to 45 s old and are used only for consumption and for the split, never as the control variable.
- **Limits.** Relative, non-persistent. A limit is sent only when it differs from the last one sent and the Inverter is reachable. After a DTU restart, limits reported by the DTU are not trusted and the current limits are re-sent once Inverters are reachable.
- **Negative setpoint.** The control never aims for more import than the AC Battery's own stored grid setpoint. If the Feed-in Setpoint asks for more, the battery's value is used and a repair issue is raised. The stored grid setpoint is never written.
- **Stuck DTU.** Stuck when the sun is above the configured angle (default 5 degrees) and none of the DTU's PV Inverters has delivered fresh data for the staleness time (default 2 minutes); or, at any sun angle, when a PV Inverter on another DTU of the same House is producing at least 50 W with fresh data while this DTU has no fresh data. Battery-backed Inverters are ignored on both sides: they are silent by day and produce at night. A DTU that does not answer on the network cannot be restarted and raises an alert instead.
- **Restart policy.** After a restart, wait 3 minutes. After three unsuccessful restarts in a row, double the wait each time up to one hour and alert. Fresh data resets this. No daily cap.
- **Failure.** The Grid Meter has failed when Grid Power is unknown, or its sensor has not reported, for three Update Intervals; a sensor repeating the same value is healthy. A House has also failed when none of the DTUs of its Inverters has answered for three Update Intervals. One DTU being down is not a House failure: the House keeps controlling the Inverters it can reach. In failure the House holds its last limits, or sets all reachable Inverters to 100 % once if that option is chosen, and raises a repair issue. Failure applies only while Curtailment is on. On Grid Meter failure the Reported Grid Power is no longer published, which makes the AC Battery idle within 60 s.
- **Curtailment off.** All Inverters of the House are set to 100 % once.
- **Reported Grid Power.** Always the true Grid Power; it is published on every meter update. Grid Charging does not alter it.
- **Grid Charging mechanism.** Charging is commanded through Victron's volatile setpoint override, rewritten every cycle and released when not charging. The Discharge Block uses the volatile override of the maximum discharge power, set to zero and released afterwards, so the stored value is never changed. Both depend on a test on the real system (see Further Notes); the fallback is an offset on the Reported Grid Power.
- **Charging Plan.** The energy to buy is the smaller of the energy missing to the Charge Target and the energy needed at sunrise. The planner picks the cheapest qualifying quarter-hours before sunrise until that energy is covered at Maximum Charge Power. It is recomputed when prices arrive, every quarter-hour, and when the charge level deviates from the plan.
- **Discharge Block.** Active in every quarter-hour whose Price Level qualifies while Grid Charging is switched on, whether or not charging is planned in it.
- **Energy needed at sunrise.** From sunrise to the start of the next qualifying price period, accumulate Expected Load minus the counted share of the PV Forecast per quarter-hour. The peak of that running sum is the energy the battery must hold at sunrise.
- **Learning.** Expected Load is a weighted average of the same quarter-hour over past days. The irradiance-to-power conversion is learned per hour of day from production history, leaving out intervals in which the House was curtailing. A configured daily consumption is the fallback for Expected Load until a week of history exists. Until two weeks of production history exist the forecast is not used and the energy needed at sunrise equals the energy missing to the Charge Target.
- **Dynamic ESS.** If active on a GX, a repair issue is raised. The integration does not switch it off.
- **Victron access.** An own MQTT connection to each GX's built-in broker (ADR 0002). The House stores the GX address; the portal id is discovered. The Reported Grid Power is published through Home Assistant's MQTT integration to the topic the grid meter driver reads.

### Defaults

| Setting | Default | Scope |
| --- | --- | --- |
| Update Interval | 15 s (5 to 60) | House |
| Tolerance band | ±30 W | House |
| Limit floor | 5 % | House |
| Maximum Charge Power | 2100 W | House |
| Charge Target | 100 % | House |
| Qualifying Price Levels | cheap, very cheap | House |
| Counted share of PV Forecast | 70 % | House |
| On failure | hold last limits | House |
| Sun angle | 5 degrees | DTU |
| Staleness time | 2 min | DTU |
| Wait after restart | 3 min | DTU |

## Testing Decisions

- A good test states what the owner would observe: given these device readings and settings, these commands go out and these entities show this. It does not assert on internal calls or structure.
- The primary seam is Home Assistant itself. Tests set up the integration in a test Home Assistant with simulated DTUs and web services at the HTTP boundary, a simulated broker at the MQTT boundary, an ordinary sensor entity as the Grid Meter, and controlled time. They assert on outgoing HTTP requests, outgoing MQTT messages, entity states and repair issues.
- The pure decisions are additionally tested directly, because their input combinations are too many to drive through Home Assistant: DTU health and restart policy, Curtailment controller, limit split, Charging planner, energy needed at sunrise, and the two learned models.
- Config and options flows are tested through Home Assistant's flow API.
- There is no prior art in the repository; these are the first tests. The tooling is the standard test harness for custom integrations.
- Only the production Home Assistant exists. Against the real system, reading is allowed at any time. Writing (limits, DTU restarts, Victron settings) happens only in sessions the owner approves each time, starting with one Inverter he names.

## Out of Scope

- Individual Inverter Limits per Inverter based on each one's production (planned later; the limit split is replaceable for this).
- Changing settings inside OpenDTU, such as switching polling off for Inverters known to be off.
- Controlling the DC Batteries or their release window.
- Power-cycling a DTU that no longer answers on the network.
- Holding back or scheduling AC Battery discharge beyond the Discharge Block.
- Tariff providers other than Tibber, and AC Batteries other than Victron ESS.
- Building it as a Supervisor app.

## Further Notes

Facts that must be established on the real system, each inside the ticket that needs it:

- Whether Home Assistant's MQTT integration uses the external broker and whether the GX topics reach it in both directions.
- Whether Victron's setpoint override expires when it is no longer refreshed, and what the AC Battery does when the grid value stops changing.
- How long after acknowledging a limit an Inverter's output actually changes.
- The sign of each House's Grid Meter sensor.
- Which of the HM-600 Inverters on the Garage DTU belongs to which House.

Known constraints from research: a DTU polls one Inverter per poll interval, so readings age with the number of Inverters; an unreachable Inverter blocks its DTU's radio for about ten seconds per attempt; after a restart a DTU reports every limit as 0 % until it has read it back; the DTUs run OpenDTU v26.3.30.
