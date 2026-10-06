# 16: Switch from Node-RED to the integration

**What to build:** Together with the owner: switch off the Node-RED flow for one House, switch on publishing of the Reported Grid Power and Grid Charging in the integration, and watch one night. Then repeat for the second House.

**Blocked by:** 14 (Charge the AC Battery in the cheapest quarter-hours), 15 (Block discharge while prices are low)

**Status:** ready-for-human

- [ ] For each House, Node-RED no longer publishes to the virtual grid meter and the integration does
- [ ] One night with a Charging Plan has been observed per House and matches the plan
- [ ] Observations and any corrections are recorded in the wiki

## Comments

2026-10-06: The first half has happened: the owner switched his previous publisher off and the integration publishes the grid power for both Houses. Still to do: switch Grid Charging on with a released version and observe one night with a Charging Plan per House.
