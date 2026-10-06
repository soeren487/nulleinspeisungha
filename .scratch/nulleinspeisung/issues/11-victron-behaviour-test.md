# 11: Test Victron override and frozen-value behaviour

**What to build:** Together with the owner, on one GX: find out whether the setpoint override expires when it is no longer refreshed, how the battery reacts to a maximum discharge power of zero while PV surplus exists, and what it does when the grid value stops changing. The outcome decides between the override mechanism and the offset fallback for tickets 14 and 15.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** resolved

- [ ] Each of the three behaviours is observed and recorded in the wiki with the Venus OS version (overrides done; the frozen grid value moved to ticket 12)
- [x] The spec states which Grid Charging mechanism is used, and why
- [x] The GX is left in its original state

## Comments

2026-10-06: Tested on one GX with the owner's go-ahead. The setpoint override and the discharge override are both honoured for an outside writer; neither expired within 200 s and 150 s without a refresh; both were released and read back as unset. A discharge limit of 0 leaves about 47 W of discharge. Decision: Grid Charging uses the setpoint override and the Discharge Block uses the discharge override, as designed; the offset on the Reported Grid Power is not needed. Because the overrides do not expire, the integration must release them itself, also at start, and the dead-man is the Reported Grid Power: when the integration stops publishing it, the grid meter driver exits after 60 s. Not tested here: the frozen or missing grid value, since another system publishes it today; that check moves to ticket 12, where the integration becomes the publisher. Also open: whether PV surplus still charges during a discharge limit of 0.
