# 06: Curtail PV Inverters to the Feed-in Setpoint

**What to build:** Per House the owner sets a signed Feed-in Setpoint and an Update Interval and switches Curtailment on. Every Update Interval the integration adjusts the Inverter Limits of the House's PV Inverters so that Grid Power stays at the setpoint within the tolerance band, all at the same percentage and never below the floor. This ticket covers a House without an AC Battery and treats all assigned Inverters as one group; battery priority and the Battery-backed group follow in tickets 08 and 09.

**Blocked by:** 04 (Create a House with Grid Meter and Inverters)

**Status:** resolved

- [x] The House has a Curtailment switch, numbers for Feed-in Setpoint and Update Interval, and sensors for control state and current Inverter Limit
- [x] Export above setpoint plus band lowers the limits; import, or export below setpoint minus band, raises them, up to 100 %
- [x] Inside the band no command is sent
- [x] Limits are relative and non-persistent; a command goes out only when the limit changes and only to reachable Inverters
- [x] Switching Curtailment off sets all the House's Inverters to 100 % once
- [x] Tolerance band and floor are configurable per House
- [x] The controller and the limit split are tested directly; the loop is tested end to end with a simulated DTU, a sensor as Grid Meter and controlled time

## Comments

2026-10-05: Implemented on branch `ticket-06-curtailment-pv`. 216 tests pass, linters clean. No limit has been sent to a real Inverter yet; that needs a session the owner approves, with an Inverter he names (ticket 10). Curtailment is off by default for a new House. The control rule was tightened in review: raising starts from the allowance only; lowering starts from the smaller of allowance and production per Inverter, and uses a production reading only when it is newer than the House's last limit change; a limit the House did not send itself is never trusted (an Inverter without one is taken to be at 100 %).
