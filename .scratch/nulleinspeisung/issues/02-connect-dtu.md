# 02: Connect a DTU and show its Inverters

**What to build:** The owner adds a DTU by address and admin password. Its Inverters then appear in Home Assistant as devices under that DTU, each showing name, model, rated power, reachability, production, data age and the current Inverter Limit. Any number of DTUs can be added. The DTU is not tied to a House.

**Blocked by:** 01 (Installable integration skeleton)

**Status:** ready-for-agent

- [ ] A DTU is added with address and password; a wrong address or password is reported in the form
- [ ] Each Inverter of the DTU appears as a device with reachability, production power, data age and Inverter Limit
- [ ] Values refresh on a fixed cadence without any House being configured
- [ ] A DTU that stops answering makes its entities unavailable and recovers by itself
- [ ] Address and password can be changed later without removing the DTU
- [ ] Tests cover the above against a simulated DTU at the HTTP boundary
