# 22: Make the Battery-backed cap and release robust against stale production

**What to build:** The cap and release of the Battery-backed group work from the House's consumption, which is computed from a fresh Grid Power and Inverter production readings that can be 10 to 45 seconds old. In a trial with Inverters that ramp as measured, a load rise while the PV Inverters were also producing made the Battery-backed group oscillate. The rules must hold steady in that situation.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] The oscillation is reproduced in an end-to-end test with the measured-behaviour simulator: Battery-backed and PV Inverters both producing, load rising and falling
- [x] The cause is established (stale production in the consumption figure, the pending change of the group, or both)
- [x] The Battery-backed group settles without repeated lowering and raising
- [x] A normal night still sends nothing

## Comments

2026-10-05: Implemented on branch `ticket-22-battery-backed-stale-production`. 585 tests pass twice, linters clean. Reproduced: with readings 20 s old the Battery-backed Inverter got 47 commands in 20 minutes. Cause: the cap acted on an Inverter without a fresh reading as if it delivered its whole allowance, and a pending lowering was over-counted because the model took the output to equal the effective limit while the source gave far less; stale readings caused a smaller remaining cycle. Fix: cap and release act only when the House is settled; the cap skips a group without a fresh reading; a pending change may cancel a deviation but not enlarge it; a reading counts as final only once the effective limit had reached the target before it was taken. Also fixed here: the release was blocked while the response reserve kept PV limits below 100 %, and a limit that is not biting was raised in many small steps; such a group now goes to 100 % in one step. After the fix: 2 commands with an AC Battery, 4 without, a normal night none. Not yet run on the real system.
