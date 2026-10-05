# 21: Account for the Inverters' ramp in Curtailment

**What to build:** Measurements show that an Inverter does not jump to a new limit: it moves its effective limit towards the target at about 0.5 % of rated power per second, in both directions, and delivers the smaller of that and what its source provides. An Inverter left at 100 % while producing 30 % therefore needs about two and a half minutes before a lower limit touches its output. Curtailment must model that effective limit per Inverter, so that it knows what a pending change will still do and does not react to it twice, and it should be able to keep limits a configurable reserve above the output so that lowering takes effect in seconds.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] The House tracks an effective limit per Inverter that moves towards the last target at a configurable rate, default 0.5 % of rated power per second
- [x] A step counts the change still on its way: a lowering or raising already commanded is not commanded again
- [x] A House setting keeps the limits a reserve above the output while not curtailing (default off), so that a lower limit bites within seconds
- [x] With a simulated Inverter that behaves as measured, a load drop is corrected without overshoot beyond the tolerance band, from 100 % and from a reserve
- [x] The rule is recorded in the spec

## Comments

2026-10-05: Implemented on branch `ticket-21-ramp-aware-curtailment`. 560 tests pass twice, linters clean. Against a simulated Inverter that behaves as measured: a load drop from 100 % is corrected with one command per Inverter and no overshoot in 195 s, with a 10 % reserve in 70 s. Two refinements over the design: a pending change may cancel a deviation but never reverse it, and the age of a production reading is taken into account when judging whether an Inverter is limited. `curtailing` now means the controller asked for less than 100 % before the reserve cap, so the reserve does not stop the PV Forecast from learning. Not yet run on the real system. Open point moved to ticket 22.
