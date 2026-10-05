# 22: Make the Battery-backed cap and release robust against stale production

**What to build:** The cap and release of the Battery-backed group work from the House's consumption, which is computed from a fresh Grid Power and Inverter production readings that can be 10 to 45 seconds old. In a trial with Inverters that ramp as measured, a load rise while the PV Inverters were also producing made the Battery-backed group oscillate. The rules must hold steady in that situation.

**Blocked by:** None (can start immediately)

**Status:** needs-triage

- [ ] The oscillation is reproduced in an end-to-end test with the measured-behaviour simulator: Battery-backed and PV Inverters both producing, load rising and falling
- [ ] The cause is established (stale production in the consumption figure, the pending change of the group, or both)
- [ ] The Battery-backed group settles without repeated lowering and raising
- [ ] A normal night still sends nothing
