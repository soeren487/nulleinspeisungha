# 30: Send fewer limit commands when the AC Battery is full and the meter is noisy

**What to build:** With a full AC Battery, more sun than the House uses and a noisy meter, the House settles and exports almost nothing, but it still sends about 30 limit commands per Inverter in 30 minutes: small lowerings and raises that follow the noise in the averaged Grid Power. Each command occupies the DTU's radio. The House should hold a limit that is good enough.

**Blocked by:** None (can start immediately)

**Status:** needs-triage

- [ ] The command count in that situation is measured on the real installation's history after ticket 29 is released
- [ ] A rule is chosen (for example a minimum change before a limit is sent again, or a tolerance band that follows the measured noise) and recorded in the spec
- [ ] In the simulated case the commands per Inverter fall to single digits per 30 minutes without more export
