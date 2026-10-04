# Build a custom integration, not a Supervisor app

The project was requested as a "Home Assistant App". Since Home Assistant 2026.2 that word means what used to be an add-on: a separate container run by the Supervisor. We build a custom integration (`custom_components`, distributed through HACS) instead, because houses, DTUs and inverters map onto config entries, devices and entities; configuration happens in the standard Settings UI; and the grid meter, MQTT and other entities are reachable in-process. An app would need its own web UI and would talk to Home Assistant from outside, and it runs on Home Assistant OS only.

## Consequences

The code runs inside Home Assistant's event loop, so it must be asynchronous Python and a fault in it can affect Home Assistant itself.
