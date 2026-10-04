# 07: Handle failures during Curtailment

**What to build:** When the Grid Meter or a DTU fails, a House holds its last Inverter Limits and raises a repair issue, or sets all Inverters to 100 % if the owner chose that. After a DTU restart the current limits are re-sent once its Inverters are reachable again.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** resolved

- [x] A Grid Meter sensor that is unavailable or unchanged for three Update Intervals puts the House into a failure state with a repair issue
- [x] In the failure state limits are held, or set to 100 % when the per-House option says so
- [x] Recovery clears the repair issue and control resumes
- [x] After a DTU restart the DTU's reported limits are ignored and the current limits are re-sent
- [x] The repair issue is also sent to the notification target when one is set

## Comments

2026-10-05: Implemented on branch `ticket-07-curtailment-failure`. 306 tests pass, linters clean. Narrowed against the original wording: one DTU being down is not a House failure, the House keeps controlling the Inverters it can reach; only "no DTU of the House answers" is, and it also waits three Update Intervals. The choice between holding and 100 % is a select entity per House. To watch on the real system: the meter counts as failed when its sensor has not reported for three Update Intervals, so a meter that reports less often than that needs a longer Update Interval.
