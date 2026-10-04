# 11: Test Victron override and frozen-value behaviour

**What to build:** Together with the owner, on one GX: find out whether the setpoint override expires when it is no longer refreshed, how the battery reacts to a maximum discharge power of zero while PV surplus exists, and what it does when the grid value stops changing. The outcome decides between the override mechanism and the offset fallback for tickets 14 and 15.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** ready-for-human

- [ ] Each of the three behaviours is observed and recorded in the wiki with the Venus OS version
- [ ] The spec states which Grid Charging mechanism is used, and why
- [ ] The GX is left in its original state
