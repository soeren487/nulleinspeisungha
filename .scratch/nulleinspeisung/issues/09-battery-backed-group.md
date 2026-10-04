# 09: Curtail Battery-backed Inverters first and cap them to the load

**What to build:** Battery-backed Inverters form their own group. When production must come down they are lowered before the PV Inverters, and when it may go up they are raised after them. They are never allowed more than the House is currently consuming, so the DC Battery neither exports nor charges the AC Battery.

**Blocked by:** 08 (Read the AC Battery and give it priority)

**Status:** ready-for-agent

- [ ] Lowering takes from the Battery-backed group until it reaches the floor before PV Inverters are touched
- [ ] Raising restores PV Inverters to 100 % before the Battery-backed group is raised
- [ ] The Battery-backed group's allowed production never exceeds House consumption computed from Grid Power, Inverter production and AC Battery power
- [ ] AC Battery headroom does not raise the Battery-backed group
- [ ] It works for a House without an AC Battery
