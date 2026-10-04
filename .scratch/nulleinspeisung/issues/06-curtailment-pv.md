# 06: Curtail PV Inverters to the Feed-in Setpoint

**What to build:** Per House the owner sets a signed Feed-in Setpoint and an Update Interval and switches Curtailment on. Every Update Interval the integration adjusts the Inverter Limits of the House's PV Inverters so that Grid Power stays at the setpoint within the tolerance band, all at the same percentage and never below the floor. This ticket covers a House without an AC Battery and treats all assigned Inverters as one group; battery priority and the Battery-backed group follow in tickets 08 and 09.

**Blocked by:** 04 (Create a House with Grid Meter and Inverters)

**Status:** ready-for-agent

- [ ] The House has a Curtailment switch, numbers for Feed-in Setpoint and Update Interval, and sensors for control state and current Inverter Limit
- [ ] Export above setpoint plus band lowers the limits; import, or export below setpoint minus band, raises them, up to 100 %
- [ ] Inside the band no command is sent
- [ ] Limits are relative and non-persistent; a command goes out only when the limit changes and only to reachable Inverters
- [ ] Switching Curtailment off sets all the House's Inverters to 100 % once
- [ ] Tolerance band and floor are configurable per House
- [ ] The controller and the limit split are tested directly; the loop is tested end to end with a simulated DTU, a sensor as Grid Meter and controlled time
