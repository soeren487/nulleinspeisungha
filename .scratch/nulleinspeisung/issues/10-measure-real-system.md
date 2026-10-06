# 10: Measure Inverter response and verify signs on the real system

**What to build:** Together with the owner, in a session he approves: measure on one Inverter he names how long after a limit command's acknowledgement the output changes; verify the sign of each House's Grid Meter sensor; record which Garage Inverter belongs to which House. Results go into the wiki and, where they change a default, into the spec.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** resolved

- [x] Response time from acknowledgement to changed output is measured for an HM and for the HMS Inverter and recorded in the wiki
- [x] The Grid Meter sign of both Houses is verified and set correctly
- [x] The Garage DTU's Inverter assignment is recorded in the wiki
- [x] The default Update Interval is confirmed or changed in the spec

## Comments

2026-10-05: First session, at night, on one Battery-backed HM-1200-4T with the owner's approval. Result in the wiki research page on limit control: lowering starts about 160 s after the command when the Inverter had been at 100 % for a while, and within 6 to 10 s shortly after an earlier change, independent of relative or absolute; raising starts within about 7 s; the output ramps at about 0.5 % of rated power per second. Follow-ups: ticket 20 (absolute limits, held) and ticket 21 (account for delay and ramp). Still open here: a panel-fed HM and the HMS by day, the Grid Meter signs, the Garage assignment, and the Update Interval default.

2026-10-05, day: Second session with the owner's approval on two panel-fed HM-600-2T of the Garage DTU, relative against absolute side by side. Result in the wiki: no difference between the two forms; the Inverter moves its effective limit at about 0.5 % of rated power per second, which explains the delays of the first session. Still open: the HM-1500 and the HMS, the Grid Meter signs, the Garage assignment.

2026-10-05, midday: Third and fourth sessions with the owner's approval for all Inverters. Measured an HM-1500-4T on each of two DTUs, the HMS-1600-4T and an HM-600-2T with 2021 firmware: all follow a limit within seconds. Only Inverters with firmware builds of 2020 slew slowly. Details in the wiki. Follow-up: ticket 23 (slew rate per Inverter). The Update Interval default of 15 s stays: DTUs read each Inverter every 5 to 20 s, so a faster loop would not see more.

Not established: which House the four HM-600 of the shared DTU feed. An experiment (lower a pair, compare both Houses' balance of AC Battery power minus Grid Power read from the GX devices) was inconclusive: the balances drifted by several hundred watts from load and sun, more than the 450 to 580 W removed. The first run of that experiment recorded nothing because of a bug in the measuring script. Waiting for the owner to confirm the assignment and the sign of each House's Grid Meter sensor in Home Assistant.

2026-10-05: The owner stated the assignment of every Inverter to its House; it is recorded in the private installation inventory. One Inverter of the shared DTU belongs to the other House than its name suggests, and one Inverter is to be left unassigned. Only the sign of each House's Grid Meter sensor is still to be confirmed by the owner.

2026-10-06: The owner confirmed that both Houses' grid power sensors are positive while importing. All points of this ticket are done.
