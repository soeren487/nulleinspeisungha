# 21: Account for the Inverters' ramp in Curtailment

**What to build:** Measurements show that an Inverter does not jump to a new limit: it moves its effective limit towards the target at about 0.5 % of rated power per second, in both directions, and delivers the smaller of that and what its source provides. An Inverter left at 100 % while producing 30 % therefore needs about two and a half minutes before a lower limit touches its output. Curtailment must model that effective limit per Inverter, so that it knows what a pending change will still do and does not react to it twice, and it should be able to keep limits a configurable reserve above the output so that lowering takes effect in seconds.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The House tracks an effective limit per Inverter that moves towards the last target at a configurable rate, default 0.5 % of rated power per second
- [ ] A step counts the change still on its way: a lowering or raising already commanded is not commanded again
- [ ] A House setting keeps the limits a reserve above the output while not curtailing (default off), so that a lower limit bites within seconds
- [ ] With a simulated Inverter that behaves as measured, a load drop is corrected without overshoot beyond the tolerance band, from 100 % and from a reserve
- [ ] The rule is recorded in the spec
