# 02: Connect a DTU and show its Inverters

**What to build:** The owner adds a DTU by address and admin password. Its Inverters then appear in Home Assistant as devices under that DTU, each showing name, model, rated power, reachability, production, data age and the current Inverter Limit. Any number of DTUs can be added. The DTU is not tied to a House.

**Blocked by:** 01 (Installable integration skeleton)

**Status:** resolved

- [x] A DTU is added with address and password; a wrong address or password is reported in the form
- [x] Each Inverter of the DTU appears as a device with reachability, production power, data age and Inverter Limit
- [x] Values refresh on a fixed cadence without any House being configured
- [x] A DTU that stops answering makes its entities unavailable and recovers by itself
- [x] Address and password can be changed later without removing the DTU
- [x] Tests cover the above against a simulated DTU at the HTTP boundary

## Comments

2026-10-04: Implemented on branch `ticket-02-connect-dtu`. 29 tests pass, linters clean. The DTU client was also run read-only against the real Büro DTU (identify, snapshot, wrong password). Not yet installed on the production Home Assistant. A DTU that is down at startup does not block the others; its devices appear on the first successful refresh. The entity tests repeat their config-entry setup in every test and would benefit from a shared fixture.
