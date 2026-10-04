# 09: Curtail Battery-backed Inverters first and cap them to the load

**What to build:** Battery-backed Inverters form their own group. When production must come down they are lowered before the PV Inverters, and when it may go up they are raised after them. They are never allowed more than the House is currently consuming, so the DC Battery neither exports nor charges the AC Battery.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** resolved

- [x] Lowering takes from the Battery-backed group until it reaches the floor before PV Inverters are touched
- [x] Raising restores PV Inverters to 100 % before the Battery-backed group is raised
- [x] The Battery-backed group's allowed production never exceeds House consumption computed from Grid Power, Inverter production and AC Battery power
- [x] AC Battery headroom does not raise the Battery-backed group
- [x] It works for a House without an AC Battery

## Comments

2026-10-05: Implemented on branch `ticket-09-battery-backed-group`. 488 tests pass, linters clean. The cap was redesigned in review. It acts on what the Battery-backed group delivers, not on what it is allowed: delivering more than the House consumes lowers the allowance to consumption; when the load returns and the allowance is what holds the group down, it is released to consumption, but only while the PV group is at 100 % or absent. A typical night (300 W delivered, more consumed) sends no limit at all. The single-group controller was removed; there is one controller for both groups. Not yet run on the real system.
