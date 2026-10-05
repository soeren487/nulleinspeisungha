# 20: Send absolute Inverter Limits

**What to build:** The integration sends each Inverter its limit as an absolute non-persistent power in watts, computed from the percent it decided and the Inverter's rated power. Measured on a real Inverter, a relative limit took minutes to lower the output while an absolute one acted within seconds. What the owner sees does not change: limits are still shown and decided in percent.

**Blocked by:** None (can start immediately)

**Status:** needs-info

- [ ] Every limit sent to a DTU is absolute and non-persistent, in whole watts: percent times rated power
- [ ] Returning an Inverter to 100 % sends its rated power
- [ ] A persistent limit still cannot be sent
- [ ] An unchanged limit is still not sent again, and all existing rules of the control loop hold
- [ ] The Inverter Limit entities still show percent

## Comments

2026-10-05: Implemented on branch `ticket-20-absolute-limits` (519 tests pass) and verified against a real Inverter, but not merged. The repeat measurement did not confirm the reason for this ticket: an absolute limit was as slow as a relative one (about 160 s) when the Inverter had been at 100 % for a while, and both act within seconds shortly after an earlier change. Waiting for a daytime measurement on a panel-fed Inverter and the owner's decision.
