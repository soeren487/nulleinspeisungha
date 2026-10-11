# 29: Limit Inverters only when power really goes to the grid

**What to build:** On the real system a House held its Inverters at 35 % while its AC Battery, far from full, charged with about 1 kW of the roughly 1.9 kW it can take: solar power was thrown away. The owner's rule: the Inverters always deliver what the sun gives, to charge the batteries and serve the House; they are limited only when their power would go to the grid; and when a load comes on while they are limited, the limit is taken back step by step.

Curtailment therefore stops relying on an estimate of what the AC Battery can take. Whenever Inverters are limited and the House is not exporting, it raises the limit a step and watches the meter: the battery and the loads take what they can, and only export that persists lowers the limit again.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] The reported situation is reproduced in a test with an AC Battery that behaves like the real one (absorbs surplus with a ramp and some jitter, up to its charge power): Inverters stay limited while the battery has room
- [x] With the new rule the same scenario ends with the battery charging at what it can take and the Inverters no more limited than the export requires
- [x] A limited House that is not exporting raises its limits step by step without waiting for an import at the meter
- [x] A load switched on while Inverters are limited is followed by rising limits within a few Update Intervals, with and without an AC Battery
- [x] Limits are lowered only for export beyond the tolerance band that persists; a spike while the AC Battery ramps up lowers nothing
- [x] With a full AC Battery and more sun than the House uses, the House settles: it does not swing, and the energy it exports while probing is small and bounded
- [x] The rule is recorded in the spec

## Comments

2026-10-11: Reported by the owner; the recorded history of his installation showed the cause. With the AC Battery regulating, the instantaneous Grid Power is noise around zero (standard deviation 67 to 112 W in calm periods, 375 W in one turbulent hour), and the House acted on one sample per Update Interval: 114 limit changes in an hour, lowered at export samples of 20 to 90 W to 5 to 15 % while the battery was discharging. The headroom suppression re-armed on that noise and kept the headroom at zero for the day.

Implemented on branch `ticket-29-curtail-only-on-export`; 914 tests pass on the pinned Home Assistant and on 2026.9.4, linters clean. With a fresh AC Battery the House regulates on the 60 s time-weighted mean of Grid Power and leaves readings from before a lowering out of it; export has to persist for two runs before limits are lowered; a held-back PV group is probed upward by 3 % of rated power with a back-off of 60 to 600 s; a discharging battery counts like an import for raising; the headroom suppression is gone. Without a fresh AC Battery the House uses the current reading as before.

In simulation with noise like the real one: with room in the battery, 6 to 8 commands per Inverter in 30 minutes and no limit below 98 %; a load switched on is covered within about 70 s with or without a battery. Not yet run on the real system. Left for ticket 30: with a full battery and a noisy meter the House still sends about 30 commands per Inverter in 30 minutes.
