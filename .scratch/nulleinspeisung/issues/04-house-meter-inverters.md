# 04: Create a House with Grid Meter and Inverters

**What to build:** The owner creates a House with a name, a location, a Grid Meter sensor and its sign, and assigns Inverters from any DTU, marking some as Battery-backed. The House appears as a device showing its Grid Power in the internal sign convention and the total production of its Inverters. Any number of Houses can exist.

**Blocked by:** 02 (Connect a DTU and show its Inverters)

**Status:** resolved

- [x] A House is created with name, location, Grid Meter sensor and sign
- [x] Inverters from several DTUs can be assigned; an Inverter already assigned to another House is not offered
- [x] Each assigned Inverter can be marked as Battery-backed
- [x] The House shows Grid Power (import positive) and total Inverter production
- [x] All of it can be changed later without removing the House
- [x] Unassigned Inverters stay visible and are untouched

## Comments

2026-10-04: Implemented on branch `ticket-04-house-meter-inverters`. A House is a subentry created in three steps (House, Inverters, Battery-backed). Beyond the ticket: separate sensors for PV and Battery-backed production and an Inverter count. Grid Power accepts W, kW, MW and mW. Not yet run on the production Home Assistant.
