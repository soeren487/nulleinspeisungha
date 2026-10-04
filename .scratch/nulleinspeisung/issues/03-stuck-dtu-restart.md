# 03: Detect and restart a Stuck DTU

**What to build:** A DTU whose PV Inverters deliver no fresh data while the sun is high enough is restarted automatically, following the stuck rule and restart policy in the spec. Each DTU shows a stuck indicator, a restart button, a restart counter and the time of the last restart. Unsuccessful restarts back off and raise a repair issue, optionally also sent to a notification target. Until Houses exist (ticket 04) every Inverter counts as a PV Inverter and the cross-DTU rule is inactive.

**Blocked by:** 02 (Connect a DTU and show its Inverters)

**Status:** resolved

- [x] With the sun above the configured angle and no fresh data from any Inverter for the staleness time, the DTU is restarted
- [x] No restart happens at night, and none when at least one Inverter delivers fresh data
- [x] After a restart nothing further happens for the waiting time; three unsuccessful restarts in a row double the wait up to one hour and raise a repair issue; fresh data clears both
- [x] The restart button restarts the DTU immediately
- [x] A DTU that does not answer on the network is not restarted and raises a repair issue
- [x] Sun angle, staleness time and waiting time are configurable per DTU
- [x] The stuck decision and restart policy are tested directly with time passed in, and end to end against a simulated DTU

## Comments

2026-10-04: Implemented on branch `ticket-03-stuck-dtu-restart`. 74 tests pass, linters clean. The restart call was run once against the real Büro DTU with the owner's approval: back after 6 s. Beyond the ticket: an "automatic restart" switch per DTU (default on), and an options flow on the main entry for the notification target. The restart policy draws no conclusion during the waiting time, because a restarted DTU reports its uptime as data age; the effective wait is at least the staleness time plus 30 s. Restart history (the backoff state) is not kept across a reload; the counter, last restart time and switch are. Not yet run on the production Home Assistant.
