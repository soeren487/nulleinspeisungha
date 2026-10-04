---
type: Research Finding
title: Shelly Pro 3EM - reading total grid power locally
description: Local interfaces of the Shelly Pro 3EM for three-phase active power, their field names, sign, update behaviour, and which value a zero feed-in controller should use.
tags: [shelly, pro-3em, grid-meter, measurement, mqtt, home-assistant]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T08:52:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: shelly-em
    resource: https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM
    title: Shelly API docs - EM component (status, config, webhooks, Modbus registers)
  - id: shelly-emdata
    resource: https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EMData
    title: Shelly API docs - EMData component (energy counters, GetNetEnergies)
  - id: shelly-pro3em
    resource: https://shelly-api-docs.shelly.cloud/gen2/Devices/Gen2/ShellyPro3EM
    title: Shelly API docs - Shelly Pro 3EM device page
  - id: shelly-channels
    resource: https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels
    title: Shelly API docs - RPC channels (HTTP, WebSocket, MQTT, UDP)
  - id: shelly-notifications
    resource: https://shelly-api-docs.shelly.cloud/gen2/General/Notifications
    title: Shelly API docs - Notifications (NotifyStatus, NotifyFullStatus, NotifyEvent)
  - id: shelly-mqtt
    resource: https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Mqtt
    title: Shelly API docs - MQTT component
  - id: shelly-modbus
    resource: https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Modbus
    title: Shelly API docs - Modbus component
  - id: shelly-changelog
    resource: https://shelly-api-docs.shelly.cloud/gen2/changelog/
    title: Shelly Gen2+ firmware changelog (newest release 2.0.1 of 2026-09-23)
    last_modified: 2026-09-23T00:00:00Z
  - id: ha-shelly-docs
    resource: https://www.home-assistant.io/integrations/shelly/
    title: Home Assistant - Shelly integration documentation
  - id: ha-shelly-source
    resource: https://github.com/home-assistant/core/tree/dev/homeassistant/components/shelly
    title: Home Assistant core, shelly component source (dev branch, read 2026-10-04; aioshelly 13.34.1)
  - id: ha-forum-frequency
    resource: https://community.home-assistant.io/t/shelly-reporting-frequency/615831
    title: Home Assistant community - Shelly reporting frequency (forum anecdotes, 2023-2025)
  - id: simon42-saldierung
    resource: https://community.simon42.com/t/shelly-pro-3em-und-richtig-saldieren-bzw-richtige-werte-in-ha/24672
    title: simon42 community - Shelly Pro 3EM und richtig saldieren (forum anecdotes; search summary only)
  - id: victron-grid-research
    resource: victron-mqtt-grid-meter.md
    title: Research page - Victron grid meter over MQTT
---

# Conclusions

- **Use `total_act_power` of component `em:0`.** It is the "Sum of the active power on all phases" in watts and is the net (saldierend) instantaneous power.[^shelly-em] A German three-phase household meter bills the net over all phases, so this is the value to regulate on, not the per-phase values (see [Net versus per-phase](#net-versus-per-phase)).
- **Sign is not stated in the vendor docs.** By convention and by all field reports, positive is import and negative is export, provided the current transformers point the right way; a per-phase `reverse` option exists to flip them.[^shelly-em][^simon42-saldierung] This must be checked once per house against a known state.
- **Three push-or-poll interfaces deliver the same status object:** HTTP RPC (poll only), WebSocket (push `NotifyStatus`), MQTT (push on `<prefix>/events/rpc` and optionally `<prefix>/status/em:0`). Modbus TCP is a fourth, poll-only option.[^shelly-channels][^shelly-mqtt][^shelly-em]
- **No vendor figure exists for the push rate.** The docs only say status change notifications are sent "on a regular interval and an error condition change".[^shelly-em] Forum reports range from about 1 s to 15 s depending on firmware.[^ha-forum-frequency] A controller that needs a guaranteed rate should poll `EM.GetStatus` (inference).
- **Home Assistant's Shelly integration is local push** over WebSocket and offers `total_act_power` as a sensor; its update rate is whatever the device pushes.[^ha-shelly-source][^ha-shelly-docs]

# The device profile matters

Since firmware 1.1.0 the Pro 3EM has two profiles: `triphase` (default) with one `EM` component (`em:0`) that "combines the readings per phase and provides totals for all phases", and `monophase` with three independent `EM1` components (`em1:0..2`) and no totals.[^shelly-pro3em] Everything below assumes `triphase`. In `monophase` the controller would have to sum three values itself.

# Fields of `EM.GetStatus` (`em:0`)

| Field | Meaning |
| --- | --- |
| `a_act_power`, `b_act_power`, `c_act_power` | "Phase A/B/C active power measurement value, [W]"[^shelly-em] |
| `total_act_power` | "Sum of the active power on all phases"[^shelly-em] |
| `a_current` …, `a_voltage` …, `a_aprt_power` …, `a_pf` …, `a_freq` … | Per-phase current, voltage, apparent power, power factor, frequency[^shelly-em] |
| `total_current`, `total_aprt_power`, `n_current` | Totals and optional neutral current[^shelly-em] |
| `errors` | `power_meter_failure`, `phase_sequence`, `ct_type_not_set`; present only if not empty[^shelly-em] |

- Values are `number or null`; a controller must treat `null` and a non-empty `errors` as "no valid measurement" (inference from the type).[^shelly-em]
- Energy counters live in the separate `emdata:0` component: `total_act` ("Total active energy on all phases, Wh") and `total_act_ret` ("Total active returned energy on all phases, Wh"), plus per-phase counters.[^shelly-emdata]

## Sign convention

- The API reference does not define which direction is positive. Its example status contains a negative per-phase value (`"b_act_power": -951.1`), so signed values are normal.[^shelly-em]
- The separate "returned energy" counters imply that negative power is energy returned to the grid (inference).[^shelly-emdata]
- Forum reports: total active power is "usually positive, but sometimes negative values when PV production exceeds household consumption".[^simon42-saldierung]
- Config `reverse: {a, b, c}`: "When set to true reverse CT measurement direction of active power and energy for phase A"; changing it requires a restart.[^shelly-em] A wrongly oriented clamp on one phase silently corrupts `total_act_power`, so commissioning must check each phase (inference).

# Interfaces

| Interface | How | Push | Notes |
| --- | --- | --- | --- |
| HTTP RPC | `GET http://<ip>/rpc/EM.GetStatus?id=0`, or POST a JSON-RPC frame to `/rpc` | No. "notifications cannot be sent and received through this channel."[^shelly-channels] | Simplest; one request per sample. No connection keepalive.[^shelly-channels] |
| WebSocket | `ws://<ip>/rpc`, JSON-RPC frames | Yes. "Clients must send at least one request frame with valid `src` to be able to receive notifications".[^shelly-channels] | `NotifyStatus` frames carry `params.ts` and the changed component, for example `params["em:0"]`.[^shelly-notifications] Requests such as `EM.GetStatus` can be sent on the same socket. The channel is "used by the local web interface and aioshelly".[^shelly-channels] "The number of simultaneous non-persistent RPC channels that can be opened on a Shelly is limited to 6"; a limit for persistent (WebSocket) connections is not stated.[^shelly-channels] |
| Outbound WebSocket | Device connects to a configured server | Yes | Home Assistant uses it for battery devices (`/api/shelly/ws`); not needed for a mains-powered Pro 3EM.[^ha-shelly-docs] |
| MQTT | Device connects to one broker | Yes | `rpc_ntf` (default true): `NotifyStatus`/`NotifyEvent` on `<device_id or topic_prefix>/events/rpc`. `status_ntf` (default false): complete component status on `<prefix>/status/em:0`, "published if a significant change occurred". RPC requests over MQTT are possible (`enable_rpc`).[^shelly-mqtt] |
| Modbus TCP | Port 502, must be enabled (`Modbus.SetConfig {"enable": true}`) | No | Input registers: 31013 total active power (float, W), 31024 / 31044 / 31064-range per phase, 31000 timestamp of last update.[^shelly-modbus][^shelly-em] Only the registers for total and phase A and B were read from the docs; phase C address is inferred from the pattern. |
| Webhooks | `total_active_power_change` and others | Event | Fires only "when the total active power has changed with at least 100W and 5% from the last reported value"; too coarse for control.[^shelly-em] |
| Device script | mJS script on the Shelly publishing at a timer | Yes | Forum workaround: script publishes EM data over MQTT at a fixed interval, minimum 1000 ms (anecdote).[^ha-forum-frequency] |

# Update rate and latency

**Vendor statements**

- EM notifications: "Statuschange notification on a regular interval and an error condition change." No interval or threshold is given.[^shelly-em]
- Firmware 1.0.2 (2023-09-11): "Pro3EM Increase report interval".[^shelly-changelog]
- Firmware 1.3.0 (2024-04-25): "EM, EM1, PM1, Switch: Throttle status change notifications when cloud is enabled".[^shelly-changelog] With Shelly Cloud enabled, pushes may therefore be slower; the amount is not documented.
- No later changelog entry up to 2.0.1 (2026-09-23) mentions the EM report interval.[^shelly-changelog]
- The internal measurement rate (how often `EM.GetStatus` returns a new sample) is not documented. No reliable answer found.

**Forum anecdotes** (Home Assistant community thread, not verified)[^ha-forum-frequency]

| When | Report |
| --- | --- |
| Firmware 0.14.x, 2023 | Updates every 1-3 s |
| Firmware 1.0.3, September 2023 | About 15 s; Shelly support said 1.0.5 addressed it |
| Firmware 1.1.0, January 2024 | 8-15 s when steady, 1-2 s when the load changes |
| November 2025 | "virtually real time, maybe 1 second delay" |

**Practical reading (agent's inference)**

- Push (WebSocket or MQTT `events/rpc`) is change-driven with an undocumented threshold and a firmware-dependent fallback interval. It is fast when power moves and sparse when it does not, which suits a controller as long as a watchdog detects silence.
- Polling `EM.GetStatus` over HTTP or an open WebSocket gives a deterministic sample age. One request per second on a LAN is a light load; no vendor limit on RPC request rate was found.
- Faster than about 1 s brings nothing for this project: the Victron side copies meter values at 1 Hz at best and ESS ramps at 400 W/s,[^victron-grid-research] and Hoymiles limit changes are slower still (covered on the OpenDTU research page).
- Latency on the wire is LAN round-trip time; no measured figures were found. The Pro 3EM has Ethernet, which avoids Wi-Fi dropouts.[^shelly-pro3em]

# Home Assistant Shelly integration

- Classified `local_push`, library `aioshelly`.[^ha-shelly-source] "Shelly devices push updates to Home Assistant upon changes for all main functions of the device"; generation 2+ devices "use the RPC protocol" and need no extra configuration when mains powered.[^ha-shelly-docs]
- Home Assistant opens the WebSocket to the device and applies each `NotifyStatus`; there is no polling of power values. Only a few diagnostic entities poll, every 60 s; reconnect interval is 60 s.[^ha-shelly-source][^ha-shelly-docs]
- Sensors defined for the `em` component include `a_act_power`, `b_act_power`, `c_act_power` and `total_act_power`.[^ha-shelly-source] Entity names depend on the device name; the total appears as "... total active power" (name not verified on a running instance).
- Consequence: an entity state is exactly as fresh as the device's last push, and Home Assistant records only changes. An app that needs its own cadence can poll the Shelly directly in addition to, or instead of, reading the entity (inference). Both can coexist within the device's connection limit.

# Net versus per-phase

- German household meters for three-phase connections are balancing meters (saldierend): the bill is the sum over the three phases, so 1000 W export on L1 and 1000 W import on L2 at the same moment count as zero. This is general domain knowledge, not taken from a source on this page; the applicable rule for Soeren's meters should be confirmed (the meter's own label or the grid operator).
- `total_act_power` is the instantaneous net over phases and is therefore the correct control variable for zero feed-in at a balancing meter.[^shelly-em] Regulating each phase to zero would be wrong and, with single-phase inverters and a single-phase MultiPlus, impossible (inference).
- The Shelly's energy counters are **not** net: forum users report that `total_act_ret` adds up the negative phases without offsetting simultaneous import on other phases, so the kWh figures differ from the utility meter.[^simon42-saldierung] Firmware 1.5.0 (2025-02-20) added `EMData.GetNetEnergies`, which returns net energies accumulated over periods of 300, 900, 1800 or 3600 s.[^shelly-changelog][^shelly-emdata] This matters for statistics and for checking the controller's result, not for the control loop.
- For the Victron virtual grid meter the same net value should be sent as total power; whether to also send per-phase values depends on the driver and on ESS phase compensation settings.[^victron-grid-research]

# Open questions for the interview

1. Firmware version and profile (`triphase`?) of both Pro 3EMs, and whether Shelly Cloud is enabled.
2. Are the Shellys already in Home Assistant through the Shelly integration, and is the device's single MQTT connection already used (for example by the Node-RED flow)?
3. Has the sign been verified (positive = import) on all three phases in both houses?
4. Should the app read the Home Assistant entity, or talk to the Shelly directly?

[^shelly-em]: Shelly API docs - EM component (status, config, webhooks, Modbus registers)
[^shelly-emdata]: Shelly API docs - EMData component (energy counters, GetNetEnergies)
[^shelly-pro3em]: Shelly API docs - Shelly Pro 3EM device page
[^shelly-channels]: Shelly API docs - RPC channels (HTTP, WebSocket, MQTT, UDP)
[^shelly-notifications]: Shelly API docs - Notifications (NotifyStatus, NotifyFullStatus, NotifyEvent)
[^shelly-mqtt]: Shelly API docs - MQTT component
[^shelly-modbus]: Shelly API docs - Modbus component
[^shelly-changelog]: Shelly Gen2+ firmware changelog (newest release 2.0.1 of 2026-09-23)
[^ha-shelly-docs]: Home Assistant - Shelly integration documentation
[^ha-shelly-source]: Home Assistant core, shelly component source (dev branch, read 2026-10-04; aioshelly 13.34.1)
[^ha-forum-frequency]: Home Assistant community - Shelly reporting frequency (forum anecdotes, 2023-2025)
[^simon42-saldierung]: simon42 community - Shelly Pro 3EM und richtig saldieren (forum anecdotes; search summary only)
[^victron-grid-research]: Research page - Victron grid meter over MQTT
