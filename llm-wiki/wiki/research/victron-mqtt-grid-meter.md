---
type: Research Finding
title: Victron grid meter over MQTT and ways to force a grid charge
description: How a MultiPlus-II GX takes an external grid power value over MQTT, how ESS uses it, and the documented alternatives to offsetting the meter value for grid charging.
tags: [victron, venus-os, mqtt, ess, grid-meter, battery]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T08:52:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: own-observation
    resource: observation on the owner's two GX devices, 2026-10-06
    title: Message cadence of a GX with an idle battery
  - id: mqtt-grid-repo
    resource: https://github.com/mr-manuel/venus-os_dbus-mqtt-grid
    title: mr-manuel/venus-os_dbus-mqtt-grid (README, config.sample.ini, dbus-mqtt-grid.py; master, last commit 2025-04-27)
    last_modified: 2025-04-27T20:47:01Z
  - id: mqtt-devices-repo
    resource: https://github.com/freakent/dbus-mqtt-devices
    title: freakent/dbus-mqtt-devices 0.9.0 (README, services.yml; main, last commit 2025-06-12)
    last_modified: 2025-06-12T13:31:11Z
  - id: nodered-virtual
    resource: https://github.com/victronenergy/node-red-contrib-victron/wiki/Virtual-devices
    title: node-red-contrib-victron wiki - Virtual devices
  - id: nodered-virtual-forum
    resource: https://community.victronenergy.com/t/virtual-devices-from-node-red/11234
    title: Victron community - Virtual devices from Node-RED (announcement by Victron developer, 2024-11-11)
    last_modified: 2024-11-11T00:00:00Z
  - id: flashmq
    resource: https://github.com/victronenergy/dbus-flashmq
    title: victronenergy/dbus-flashmq README (master, last commit 2026-08-21)
    last_modified: 2026-08-21T13:14:01Z
  - id: ess-mode23
    resource: https://www.victronenergy.com/live/ess:ess_mode_2_and_3
    title: Victron - ESS mode 2 and 3 (page last modified 2024-10-14)
    last_modified: 2024-10-14T15:20:00Z
  - id: ess-manual
    resource: https://www.victronenergy.com/media/pg/Energy_Storage_System/en/configuration.html
    title: Victron ESS manual - Configuration and FAQ chapters
  - id: venus-dbus
    resource: https://github.com/victronenergy/venus/wiki/dbus
    title: Venus OS wiki - D-Bus services and paths
  - id: systemcalc-schedule
    resource: https://github.com/victronenergy/dbus-systemcalc-py/blob/master/delegates/schedule.py
    title: dbus-systemcalc-py - scheduled charging delegate (and dynamicess.py)
  - id: modbus-attributes
    resource: https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv
    title: dbus_modbustcp attributes.csv (register to D-Bus path mapping)
  - id: override-forum
    resource: https://community.victronenergy.com/t/3-50-24-modification-modbustcp-volatile-register-for-gridsetpoint-what-about-mqtt/4816
    title: Victron community - volatile register for grid setpoint, what about MQTT (forum; includes Victron staff reply, 2024-09-23)
    last_modified: 2024-09-23T00:00:00Z
  - id: ha-victron-gx
    resource: https://www.home-assistant.io/integrations/victron_gx/
    title: Home Assistant - Victron GX integration
  - id: initial-requirements
    resource: ../../raw/2026-10-04-initial-requirements.md
    title: Initial requirements stated by Soeren
---

# Conclusions

- **Which "virtual MQTT grid meter" is installed is not known.** Venus OS has no built-in "grid meter from MQTT" setting; three mechanisms exist (table below). The wording "installed the virtual MQTT Grid meter"[^initial-requirements] fits the community driver `mr-manuel/venus-os_dbus-mqtt-grid` best, whose default device name is "MQTT Grid" (agent's inference). Ask Soeren; the answer decides topic, payload and timeout behaviour.
- **Stale data is the main hazard.** ESS regulates on whatever the grid service last reported. With `dbus-mqtt-grid` the driver exits 60 s after the last MQTT message, the grid meter disappears and ESS goes to pass-through.[^mqtt-grid-repo][^ess-manual] With `dbus-mqtt-devices` no data timeout was found; the last value stays.[^mqtt-devices-repo]
- **The offset trick has a documented replacement.** ESS has a grid setpoint: "Positive: take power from grid".[^ess-mode23] The volatile variant is `com.victronenergy.hub4 /Overrides/Setpoint`, MQTT topic `W/<portal id>/hub4/0/Overrides/Setpoint`, which "can also be used by users when DynamicEss is not used".[^venus-dbus] Scheduled charging (five windows with SoC target) is the second documented option.[^ess-manual]
- **ESS response is rate-limited** to 400 W per second in the inverter firmware, plus grid-code ramp limits.[^ess-mode23]
- **Not found:** an official statement of how long hub4control tolerates a grid meter that is present but no longer updating, and whether `/Overrides/Setpoint` has a timeout. hub4control is not open source. Both need a test on the real system.

# Candidate mechanisms for the virtual grid meter

| Mechanism | How values get in | Timeout | Status |
| --- | --- | --- | --- |
| `venus-os_dbus-mqtt-grid` (mr-manuel), installed on the GX | Driver is an MQTT *client*; it subscribes to one configurable topic on a configurable broker (may be the GX's own broker or the Home Assistant broker) and publishes `com.victronenergy.grid.mqtt_grid` on D-Bus.[^mqtt-grid-repo] | `timeout = 60` s default, `0` disables. "Specify after how many seconds the driver should exit (disconnect), if no new MQTT message was received".[^mqtt-grid-repo] | Community project. Supports "the latest three stable versions of Venus OS".[^mqtt-grid-repo] |
| `dbus-mqtt-devices` (freakent), installed on the GX | Client registers by publishing to `device/<client id>/Status` on the GX broker, receives portal id and device instance on `device/<client id>/DBus`, then writes each value to `W/<portal id>/grid/<device instance>/<path>`.[^mqtt-devices-repo] | Device is removed only when a status message with `connected: 0` arrives (intended as MQTT last will). No value-age timeout documented.[^mqtt-devices-repo] | Community project, v0.9.0 required for Venus OS 3.60 and later.[^mqtt-devices-repo] |
| Node-RED "Virtual device" node (Venus OS Large) | Node-RED flow on the GX sends an object keyed by D-Bus path to the virtual device node; service name `com.victronenergy.<type>.virtual_<nodeId>`. Grid meter is one of the types.[^nodered-virtual] MQTT input would be an ordinary MQTT-in node in the same flow (inference). | Not documented. `msg.connected = false` takes the device offline.[^nodered-virtual] | Official Victron, shipped from Venus OS 3.60 beta, announced 2024-11-11.[^nodered-virtual-forum] |

## Payloads

`dbus-mqtt-grid`: one JSON message per update on the configured topic. Minimum `{"grid": {"power": 0.0}}`; optional `voltage`, `current`, `energy_forward`, `energy_reverse` and per-phase objects `L1`, `L2`, `L3` with `power`, `voltage`, `current`, `frequency`, `power_factor`, `energy_forward`, `energy_reverse`.[^mqtt-grid-repo] Sign: "positive: consumption, negative: feed into grid" (source comment).[^mqtt-grid-repo] Values outside `power_threshold_per_phase` (default 23000 W per phase) are rejected.[^mqtt-grid-repo] If only total power is sent, the driver copies it to L1.[^mqtt-grid-repo]

`dbus-mqtt-devices`: one message per D-Bus path, payload `{"value": <number>}`. Grid paths include `Ac/Power`, `Ac/L1/Power`, `Ac/L2/Power`, `Ac/L3/Power`, voltages, currents and energy counters.[^mqtt-devices-repo] The same payload format is used by every `W/` topic of the GX broker.[^flashmq]

Grid service paths that systemcalc and ESS read: `/Ac/Power` ("total of all phases, real power"), `/Ac/L1..L3/Power`, `/Ac/Energy/Forward` (bought), `/Ac/Energy/Reverse` (sold).[^venus-dbus]

## Update rate

- `dbus-mqtt-grid` copies the latest MQTT value to D-Bus in a 1000 ms timer and increments `/UpdateIndex` each second, whether or not a new message arrived.[^mqtt-grid-repo] Publishing faster than once per second therefore gains nothing with this driver; about 1 Hz is the useful rate (inference from the source).
- No required minimum rate is documented for `dbus-mqtt-devices` or the Node-RED virtual device.
- Forum reports of ESS oscillating with slow or delayed meter values exist, but none was verified for this page; treat as anecdote.

## When values stop

| Case | Behaviour | Basis |
| --- | --- | --- |
| Grid service disappears (driver exited, device unregistered) | ESS with "External meter" puts the VE.Bus system in pass-through: "When the GX device is no longer receiving data from the grid meter. Note that this is only for systems that are configured to have an external grid meter." | ESS manual FAQ Q4[^ess-manual] |
| `dbus-mqtt-grid`, messages stop | Last value is held for up to `timeout` seconds (default 60), then the driver exits. After restart it waits for a first message and exits again if none arrives within the timeout, so the service stays absent. | Driver source[^mqtt-grid-repo] |
| Service present but value frozen | ESS keeps regulating against the frozen value. A frozen import of +500 W makes ESS add discharge power without the meter ever responding, so battery power drifts to its limit (agent's inference; no Victron statement found). | Inference |
| Home Assistant or the new app restarts | Same as "messages stop". The app should either keep publishing through restarts or deliberately let the timeout fire (inference). | Inference |

# How ESS uses the grid meter

- Mode 1 (normal ESS): "the ESS control system tries to keep the power flowing through the grid meter at 0 Watt"; Mode 2 means "you actively control the target for the grid power. Setting the target to 100 Watt means that the system tries to take 100 Watt from the grid. The power will be used to feed the loads or charge the battery."[^ess-mode23]
- Grid setpoint default is 50 W, "slightly above 0W prevents the system from feeding back power to the grid when there is a bit of over-shoot in the regulation".[^ess-manual]
- Grid metering setting: "External meter" or "Inverter/Charger"; D-Bus `/Settings/CGwacs/RunWithoutGridMeter` (0 = external meter, 1 = inverter/charger).[^ess-manual][^modbus-attributes]
- Speed: "Hard coded rate limiting in the inverter/charger firmware: as per ESS version 162 it is set to 400W per second", in addition to "various ramp-up and ramp-down restrictions, enforced by grid-codes".[^ess-mode23] A 2100 W step therefore takes at least about 5 s (arithmetic).
- Offsetting the meter and moving the setpoint are equivalent to the regulator: adding -X W to the reported grid power makes ESS import X W more to bring the reported value back to the setpoint (inference). The difference is in side effects, see below.

# Alternatives to the offset trick

All topics are on the GX's own broker; `<id>` is the portal id. Writes use `W/…` with payload `{"value": …}`.[^flashmq]

| Option | Topic / path | Notes |
| --- | --- | --- |
| Volatile grid setpoint override | `W/<id>/hub4/0/Overrides/Setpoint` (D-Bus `com.victronenergy.hub4 /Overrides/Setpoint`, Modbus 2716, int32, W) | "Used by DynamicEss to override the AC Power setpoint; Can also be used by users when DynamicEss is not used."[^venus-dbus][^modbus-attributes] Added in Venus OS 3.50 "to avoid constantly logging the new setpoint to VRM, and wearing the flash on the GX-device with repeated writes".[^ess-mode23] Victron's own code clears it by writing `None`.[^systemcalc-schedule] Forum: a Victron staff member was "99% sure" this is the MQTT path; a user reports it sticks with Dynamic ESS off and is reset to null with Dynamic ESS on.[^override-forum] Device instance `0` for hub4 is the usual value and should be confirmed with a wildcard subscription (inference). |
| Persistent grid setpoint | `W/<id>/settings/0/Settings/CGwacs/AcPowerSetPoint` (Modbus 2700) | The user setting shown in the GUI. Positive = take from grid.[^ess-mode23] Stored in settings, hence the flash-wear warning above for frequent writes.[^ess-mode23] Survives a reboot, so a crash of the app leaves the battery charging from the grid until someone resets it (inference). |
| Scheduled charging | `W/<id>/settings/0/Settings/CGwacs/BatteryLife/Schedule/Charge/<0..4>/{Day,Start,Duration,Soc,AllowDischarge}` | "up to five scheduled periods, during which the system will take power from the grid to charge the battery"; stops "after the set duration or when the set SoC limit is reached".[^ess-manual] `Start` is seconds since midnight, `Duration` seconds, `Soc` 0-100, `Day` 0-6 weekday (Sunday = 0), 7 every day, 8 weekdays, 9 weekend; the default is -7 (range -11 to 11), and a negative value appears to mean "disabled" (inference from the default, not stated in the source).[^systemcalc-schedule] Runs on the GX itself, so it survives loss of Home Assistant. No charge power parameter exists per window, so it charges at whatever the charge limits allow (inference); system state 259 is "Scheduled recharge".[^venus-dbus] |
| Force-charge flag | `W/<id>/hub4/0/Overrides/ForceCharge` | "Used by scheduled charging and DynamicEss to activate charging".[^venus-dbus] Not documented for user writes; the scheduler rewrites it every 5 s when it has control.[^systemcalc-schedule] Avoid unless tested. |
| Charge power cap | `W/<id>/settings/0/Settings/CGwacs/MaxChargePower`; DVCC current limit `…/Settings/SystemSetup/MaxChargeCurrent` | "User setting: Max Charge Power".[^venus-dbus] Useful to charge slower than the roughly 2100 W maximum.[^initial-requirements] |
| Mode 3 (external control) | Set `…/Settings/CGwacs/Hub4Mode` = 3, then write `W/<id>/vebus/<instance>/Hub4/L1/AcPowerSetpoint`; `Hub4/DisableCharge`, `Hub4/DisableFeedIn` | The app becomes the whole control loop; no grid meter needed. "These registers must be written once every 60 seconds, or the Multi will go into Passthru." BatteryLife is disabled and a managed battery's discharge limit is ignored.[^ess-mode23] More work and more risk than Mode 2; not needed for this use case (inference). |
| Minimum SoC | `…/Settings/CGwacs/BatteryLife/MinimumSocLimit` | Raising it triggers recharge from grid only when SoC is 5 % or more below it; coarse.[^ess-manual] |

Trade-offs versus the offset trick (agent's assessment):

- The setpoint override is the documented control point, leaves the grid meter truthful (VRM and GX statistics stay correct), and is volatile. The offset trick falsifies the grid reading and every statistic derived from it.
- The offset trick needs no write access to GX settings and ends automatically when the meter driver times out. The override's behaviour when the writer disappears is not documented; a test is needed, or use scheduled charging as the fail-safe mechanism.
- Scheduled charging cannot follow 15-minute price slots finely unless the app rewrites the schedule, and each rewrite is a settings write.

# MQTT on the GX: keepalive

- **Observed on Venus OS v3.67 (2026-10-06):** with the keepalive pattern below, the GX publishes each value once and afterwards only when it changes. A battery that is idle at exactly 0 W sent none of battery power, charge level or voltage in 90 s. `N/<portal id>/heartbeat` (payload `{"value": <unix time>}`) arrived every 3 s throughout. Liveness of the GX must therefore be judged by the heartbeat, not by the arrival of values.[^own-observation]

- Topics: `N/<id>/<service>/<instance>/<path>` notifications, `R/…` read requests, `W/…` writes, payload `{"value": …}`.[^flashmq]
- "To activate keep-alive, send a read request to `R/<portal ID>/keepalive`"; "Keep-alive timeout is 60 seconds."[^flashmq] Without it the GX stops publishing `N/` topics. Nothing is retained; a keepalive with empty payload triggers a full republish that ends with `N/<id>/full_publish_completed`.[^flashmq]
- Recommended pattern: first keepalive with empty payload, then every 30 s with `{ "keepalive-options" : ["suppress-republish"] }`.[^flashmq]
- The README describes keepalive as governing notifications; it does not say writes need it (inference: `W/` works without, to be confirmed).
- Mode 3 has its own, separate 60 s rule for the setpoint itself.[^ess-mode23]

# Topics for battery and inverter state

| Value | Topic | Notes |
| --- | --- | --- |
| Battery SoC | `N/<id>/system/0/Dc/Battery/Soc` | System-level, from the selected battery monitor.[^venus-dbus] |
| Battery power | `N/<id>/system/0/Dc/Battery/Power` | Battery service: "positive when charged, negative when discharged".[^venus-dbus] |
| Battery voltage, current | `N/<id>/system/0/Dc/Battery/Voltage`, `…/Current` | [^venus-dbus] |
| Charge / discharge current limits (BMS) | `N/<id>/battery/<inst>/Info/MaxChargeCurrent`, `…/Info/MaxDischargeCurrent`, `…/Info/MaxChargeVoltage` | Published only by managed batteries.[^venus-dbus] |
| Active ESS power limits | `N/<id>/hub4/0/MaxChargePower`, `…/MaxDischargePower` | "Active maximum charge/discharge power limit".[^venus-dbus] |
| System state | `N/<id>/system/0/SystemState/State` | 3 bulk, 4 absorption, 5 float, 8 passthru, 9 inverting, 252 external control, 256 discharging, 257 sustain, 258 recharge, 259 scheduled recharge.[^venus-dbus] |
| Inverter/charger state | `N/<id>/vebus/<inst>/State`, `…/Mode` | State as above (0-11, 244, 252); Mode 1 charger only, 2 inverter only, 3 on, 4 off.[^venus-dbus] |
| Multi DC power | `N/<id>/vebus/<inst>/Dc/0/Power` | "positive when charging".[^venus-dbus] |
| Multi AC input power | `N/<id>/vebus/<inst>/Ac/ActiveIn/P` | Total power at AC-in.[^venus-dbus] |
| Grid as seen by the system | `N/<id>/system/0/Ac/Grid/L1/Power` (and L2, L3) | Shows what the virtual meter delivers.[^venus-dbus] |
| ESS mode / state | `N/<id>/settings/0/Settings/CGwacs/Hub4Mode`, `…/BatteryLife/State` | Hub4Mode 1/2 = ESS, 3 = external control; State 9 = keep batteries charged, 10 = optimized without BatteryLife.[^venus-dbus] |

Home Assistant 2026.5 added an official Victron GX integration that connects to the GX broker and exposes such values as entities ("Local Push"); it applies a debounce so "rapidly changing values may appear with a short delay".[^ha-victron-gx] Whether it exposes `/Overrides/Setpoint` was not checked.

# Open questions for the interview

1. Which of the three mechanisms is installed, on which Venus OS version, and which broker and topic does it read?
2. Is ESS set to "External meter", and is Dynamic ESS off?
3. Is a single-phase MultiPlus-II fed a three-phase net value as total only, or per phase? (See [shelly-pro-3em-measurement.md](shelly-pro-3em-measurement.md).)
4. What should happen to the battery when the app or Home Assistant is down: pass-through, or normal ESS on a truthful meter?

[^mqtt-grid-repo]: mr-manuel/venus-os_dbus-mqtt-grid (README, config.sample.ini, dbus-mqtt-grid.py; master, last commit 2025-04-27)
[^mqtt-devices-repo]: freakent/dbus-mqtt-devices 0.9.0 (README, services.yml; main, last commit 2025-06-12)
[^nodered-virtual]: node-red-contrib-victron wiki - Virtual devices
[^nodered-virtual-forum]: Victron community - Virtual devices from Node-RED (announcement by Victron developer, 2024-11-11)
[^flashmq]: victronenergy/dbus-flashmq README (master, last commit 2026-08-21)
[^ess-mode23]: Victron - ESS mode 2 and 3 (page last modified 2024-10-14)
[^ess-manual]: Victron ESS manual - Configuration and FAQ chapters
[^venus-dbus]: Venus OS wiki - D-Bus services and paths
[^systemcalc-schedule]: dbus-systemcalc-py - scheduled charging delegate (and dynamicess.py)
[^modbus-attributes]: dbus_modbustcp attributes.csv (register to D-Bus path mapping)
[^override-forum]: Victron community - volatile register for grid setpoint, what about MQTT (forum; includes Victron staff reply, 2024-09-23)
[^ha-victron-gx]: Home Assistant - Victron GX integration
[^initial-requirements]: Initial requirements stated by Soeren
[^own-observation]: Message cadence of a GX with an idle battery
