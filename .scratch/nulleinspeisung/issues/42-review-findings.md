# 42: Findings of the code review of 2026-11-13

**What to build:** Fixes for what a full read of the integration found. Each item names the place and the failing case.

**Blocked by:** 40, 41

**Status:** resolved

Control and load:

- [x] **Replanning on every GX message.** `GridCharging._on_battery`: while the charge level is at or above the Charge Target and the plan's reason is not "at target" (a full battery on a sunny day), every message of the GX (one to three a second) makes a full plan. Replan only when that condition flips, as the 1 % step does.
- [x] **One plan per moment, off the event loop.** At a quarter-hour boundary the prices, the Expected Load, the PV Forecast, the clock and the stale check each trigger their own full plan per House (4 to 6 plans; a plan for 48 hours takes 40 to 375 ms on a desktop, several times that on small hardware). Collect the triggers into one planning run per House (the settings and the charging quarter-hour keep their immediate effect), and run `plan_charging` in the executor or make it cheap enough (for example `_feasible` without bisection, simulation from the changed index on). Measure before and after and record the numbers in the ticket.
- [x] **A failing plan must not stop the others.** An exception in a listener of the gateway, the publisher or Grid Charging aborts the remaining listeners for that message. Catch and log per listener; a failed plan keeps the previous plan.
- [x] **Top-up reached is remembered over a restart and a reload.** Kept in memory only, so a restart or any change of the configuration (which reloads the entry) plans a second top-up on the same day. Persist the day it was reached and a carried window.
- [x] **Limits after unload and restart.** Unloading, disabling or removing the entry leaves the Inverters at their last limit until night. After a reload or restart the House assumes 100 %, and with a full AC Battery that covers the load no raise happens, so the Inverters stay low while the battery drains. On a real unload (not a reload) and when Home Assistant stops, send 100 % to the reachable assigned Inverters; at start, take a limit below 100 % that the DTU reports for an assigned Inverter as the House's own.

Smaller:

- [x] `_reason` can say "at target" for a low battery when an earlier shortfall was given up for the efficiency and a later one is restricted to quarter-hours after it: give the true reason.
- [x] DC Battery Support in the quarter-hour that contains the sunrise is the mean of the part before the sunrise but applied to the whole quarter-hour: pass what applies to the whole quarter-hour.
- [x] Diagnostics still show the Grid Meter's entity id, the DC Battery sensors' entity ids (default Shelly ids contain the device's MAC) and House titles in the subentries: replace them by placeholders.
- [x] `alerts.py`: formatting the notification text can raise outside the `try`; a flapping issue repeats the notification every time. Format inside the `try`; at most one notification per issue per hour.
- [x] Reconfiguring a DTU asks for the password again: an empty password keeps the stored one. Reconfiguring a House probes the GX even when host and port did not change: skip the probe then.
- [x] The reason sensor's `text`, `text_de` and time attributes are not recorded; the reason is computed once per update.
- [x] Clean-up without changing entity ids: one module for the quarter-hour helpers (`quarter_start`/`slot_start` exist three times, `SLOT` in six modules, `slot_length` twice); stale docstrings (`switch.py` "charges to 95 %", `sensor.py` module docstring and the list that omits `top_up_energy`, `house.py` "Later features read"); `sunrise_energy.py` keeps only what is still used, under a fitting name.

Documentation:

- [x] `README.md`: what the integration does today (features, entities per device, setup of DTUs, Houses, GX and the grid meter topic, Dynamic ESS warning, a setting whose entity is disabled falls back to its default, behaviour on unload), installation through HACS, the tools in `tools/`.
- [x] `.scratch/nulleinspeisung/spec.md`: the Solution paragraph, user stories 44, 46 and 57 and the testing section still describe charging before sunrise; bring them in line with the Rules section. Note that a charging quarter-hour charges at the Maximum Charge Power (or the Safety Buffer's power) for as long as the plan says, and that PV surplus in a charging quarter-hour is not counted.
- [x] Wiki: `project/overview.md` (releases, tickets, purpose, new tools), `project/requirements-decisions.md` (rows superseded by shortfall planning, discharge block on demand, top-up to a level), `log.md`.

Not in this ticket (recorded for later): splitting `grid_charging.py` and `charging_planner.py` into smaller modules; renaming the entities "Battery at sunrise" and "Forecast surplus", which would break the owner's dashboard and history.

## Comments

2026-11-13: Implemented. Planner timings for 192 quarters on the development machine, before → after: 6 to 158 ms → 3 to 76 ms, results unchanged (all planner tests unmodified, 12 benchmark plans identical); a quarter-hour boundary makes 1 to 2 plans per House instead of 4 to 6. Not done: the executor and dropping the second plain plan (small gain). Limits on stop follow the House's failure behaviour (hold: nothing sent; 100 %: released) so that a restart of Home Assistant does not export; disabling and removing send 100 %. Known gap: a DTU that restarted shortly before Home Assistant starts and reports a low non-zero limit is taken over. 1056 tests pass on the pinned Home Assistant.
