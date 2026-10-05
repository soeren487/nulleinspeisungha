# 23: Set the limit slew rate per Inverter

**What to build:** Measurements show two kinds of Inverter in one House: those with firmware builds of 2020 move their effective limit at about 0.5 % of rated power per second, those with later firmware are at a new limit within seconds. The slew rate therefore belongs to each Inverter, not to the House. Each Inverter gets its own setting, with a default guessed from the firmware build date its DTU reports, and the House's model, pending change and response reserve use the Inverter's own rate.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Each Inverter device has a limit slew rate setting that survives restarts
- [ ] Its default comes from the firmware build date: slow for builds before 2021, fast otherwise, and slow while the date is unknown
- [ ] The firmware build date is shown on the Inverter
- [ ] The House uses each Inverter's own rate for its effective limit and pending change; the House-wide setting is gone
- [ ] The response reserve applies only to Inverters that slew slowly
- [ ] A House with slow and fast Inverters corrects a load drop without waiting on the fast ones and without overshoot
- [ ] The rule is recorded in the spec
