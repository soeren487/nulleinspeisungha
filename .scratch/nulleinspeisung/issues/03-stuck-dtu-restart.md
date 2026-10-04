# 03: Detect and restart a Stuck DTU

**What to build:** A DTU whose PV Inverters deliver no fresh data while the sun is high enough is restarted automatically, following the stuck rule and restart policy in the spec. Each DTU shows a stuck indicator, a restart button, a restart counter and the time of the last restart. Unsuccessful restarts back off and raise a repair issue, optionally also sent to a notification target. Until Houses exist (ticket 04) every Inverter counts as a PV Inverter and the cross-DTU rule is inactive.

**Blocked by:** 02 (Connect a DTU and show its Inverters)

**Status:** ready-for-agent

- [ ] With the sun above the configured angle and no fresh data from any Inverter for the staleness time, the DTU is restarted
- [ ] No restart happens at night, and none when at least one Inverter delivers fresh data
- [ ] After a restart nothing further happens for the waiting time; three unsuccessful restarts in a row double the wait up to one hour and raise a repair issue; fresh data clears both
- [ ] The restart button restarts the DTU immediately
- [ ] A DTU that does not answer on the network is not restarted and raises a repair issue
- [ ] Sun angle, staleness time and waiting time are configurable per DTU
- [ ] The stuck decision and restart policy are tested directly with time passed in, and end to end against a simulated DTU
