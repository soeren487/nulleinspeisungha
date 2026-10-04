# 21: Account for the Inverters' ramp in Curtailment

**What to build:** After a limit change an Inverter's output moves at roughly 0.5 % of its rated power per second, so a large change takes most of a minute. Curtailment must not react again to the part of a change that is still on its way, or it overshoots and swings. The House counts the change still in flight when it decides the next step.

**Blocked by:** 20 (Send absolute Inverter Limits)

**Status:** needs-triage

- [ ] The ramp rate is confirmed by day on a panel-fed HM Inverter and on the HMS Inverter (ticket 10)
- [ ] The control rule for a change in flight is designed and recorded in the spec
- [ ] A lowering step is not repeated while the earlier one is still ramping
- [ ] A simulated Inverter that ramps shows no overshoot beyond the tolerance band in the end-to-end tests
