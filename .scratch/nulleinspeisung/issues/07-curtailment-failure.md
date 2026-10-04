# 07: Handle failures during Curtailment

**What to build:** When the Grid Meter or a DTU fails, a House holds its last Inverter Limits and raises a repair issue, or sets all Inverters to 100 % if the owner chose that. After a DTU restart the current limits are re-sent once its Inverters are reachable again.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** ready-for-agent

- [ ] A Grid Meter sensor that is unavailable or unchanged for three Update Intervals puts the House into a failure state with a repair issue
- [ ] In the failure state limits are held, or set to 100 % when the per-House option says so
- [ ] Recovery clears the repair issue and control resumes
- [ ] After a DTU restart the DTU's reported limits are ignored and the current limits are re-sent
- [ ] The repair issue is also sent to the notification target when one is set
