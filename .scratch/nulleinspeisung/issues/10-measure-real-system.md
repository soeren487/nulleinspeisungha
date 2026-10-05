# 10: Measure Inverter response and verify signs on the real system

**What to build:** Together with the owner, in a session he approves: measure on one Inverter he names how long after a limit command's acknowledgement the output changes; verify the sign of each House's Grid Meter sensor; record which Garage Inverter belongs to which House. Results go into the wiki and, where they change a default, into the spec.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** ready-for-human

- [ ] Response time from acknowledgement to changed output is measured for an HM and for the HMS Inverter and recorded in the wiki (HM-1200-4T done 2026-10-05; a panel-fed HM and the HMS are still open)
- [ ] The Grid Meter sign of both Houses is verified and set correctly
- [ ] The Garage DTU's Inverter assignment is recorded in the wiki
- [ ] The default Update Interval is confirmed or changed in the spec

## Comments

2026-10-05: First session, at night, on one Battery-backed HM-1200-4T with the owner's approval. Result in the wiki research page on limit control: lowering starts about 160 s after the command when the Inverter had been at 100 % for a while, and within 6 to 10 s shortly after an earlier change, independent of relative or absolute; raising starts within about 7 s; the output ramps at about 0.5 % of rated power per second. Follow-ups: ticket 20 (absolute limits, held) and ticket 21 (account for delay and ramp). Still open here: a panel-fed HM and the HMS by day, the Grid Meter signs, the Garage assignment, and the Update Interval default.
