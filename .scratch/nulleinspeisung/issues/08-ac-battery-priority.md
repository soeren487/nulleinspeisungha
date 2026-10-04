# 08: Read the AC Battery and give it priority

**What to build:** A House can be given an AC Battery. The integration reads its charge level, power, stored grid setpoint and Dynamic ESS mode over MQTT, shows them, and uses them in Curtailment: while the battery can still absorb more, PV Inverters are not curtailed below what it could take. A negative Feed-in Setpoint never aims for more import than the battery's own grid setpoint. This ticket first establishes with the owner how the GX topics reach Home Assistant and uses that path.

**Blocked by:** 06 (Curtail PV Inverters to the Feed-in Setpoint)

**Status:** resolved

- [x] It is established and recorded in the wiki whether Home Assistant's MQTT integration carries the GX topics in both directions; the access path follows from that
- [x] A House accepts a portal ID, usable capacity and Maximum Charge Power; the House shows charge level and battery power
- [x] The GX's values are kept alive
- [x] With the battery below full and charging below Maximum Charge Power, Curtailment leaves room for the remaining charge power
- [x] With the battery full or at Maximum Charge Power, Curtailment behaves as in ticket 06
- [x] A Feed-in Setpoint asking for more import than the battery's grid setpoint uses the battery's value and raises a repair issue
- [x] Active Dynamic ESS raises a repair issue
- [x] Nothing is written to the GX in this ticket

## Comments

2026-10-05: Implemented on branch `ticket-08-ac-battery-priority`. 448 tests pass, linters clean. The access path was settled first (ADR 0002): an own MQTT connection to each GX. The probe was run read-only against a real GX and found its portal id. Nothing is written to the GX except the keepalive. Battery headroom also respects the BMS charge current limit. A guard handles a battery that does not absorb surplus: one run with export beyond the band is waited out (the battery ramps), a second consecutive one lowers the limits and ignores the headroom for 5 minutes. Not yet run with Curtailment on the real system.
