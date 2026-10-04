# 04: Create a House with Grid Meter and Inverters

**What to build:** The owner creates a House with a name, a location, a Grid Meter sensor and its sign, and assigns Inverters from any DTU, marking some as Battery-backed. The House appears as a device showing its Grid Power in the internal sign convention and the total production of its Inverters. Any number of Houses can exist.

**Blocked by:** 02 (Connect a DTU and show its Inverters)

**Status:** ready-for-agent

- [ ] A House is created with name, location, Grid Meter sensor and sign
- [ ] Inverters from several DTUs can be assigned; an Inverter already assigned to another House is not offered
- [ ] Each assigned Inverter can be marked as Battery-backed
- [ ] The House shows Grid Power (import positive) and total Inverter production
- [ ] All of it can be changed later without removing the House
- [ ] Unassigned Inverters stay visible and are untouched
