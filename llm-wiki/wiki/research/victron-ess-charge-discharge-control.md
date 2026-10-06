---
type: Research Finding
title: Victron ESS - blocking discharge and charging from the grid over MQTT
description: MQTT-writable ESS settings on Venus OS 3.67 that stop battery discharge while charging continues, how to charge from the grid at a given power, persistence of each setting, and the conflict with Dynamic ESS.
tags: [victron, venus-os, mqtt, ess, battery, grid-charging]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T09:36:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: own-test
    resource: test on the owner's installation, 2026-10-06
    title: Override test on a MultiPlus-II GX with Venus OS v3.67
  - id: venus-dbus
    resource: https://github.com/victronenergy/venus/wiki/dbus
    title: Venus OS wiki - D-Bus services and paths
  - id: ess-mode23
    resource: https://www.victronenergy.com/live/ess:ess_mode_2_and_3
    title: Victron - ESS mode 2 and 3
  - id: modbus-attributes
    resource: https://github.com/victronenergy/dbus_modbustcp/blob/master/attributes.csv
    title: dbus_modbustcp attributes.csv (register to D-Bus path mapping, master, fetched 2026-10-04)
  - id: ess-manual
    resource: https://www.victronenergy.com/media/pg/Energy_Storage_System/en/configuration.html
    title: Victron ESS manual - Configuration chapter
  - id: systemcalc-schedule
    resource: https://github.com/victronenergy/dbus-systemcalc-py/blob/master/delegates/schedule.py
    title: dbus-systemcalc-py - delegates/schedule.py (master, fetched 2026-10-04)
  - id: systemcalc-dess
    resource: https://github.com/victronenergy/dbus-systemcalc-py/blob/master/delegates/dynamicess.py
    title: dbus-systemcalc-py - delegates/dynamicess.py (master, fetched 2026-10-04)
  - id: localsettings
    resource: https://github.com/victronenergy/localsettings/blob/master/localsettings.py
    title: victronenergy/localsettings - localsettings.py (master)
  - id: dess-manual
    resource: https://www.victronenergy.com/live/drafts:dynamic_ess
    title: Victron - Dynamic ESS manual (page last modified 2026-09-04)
  - id: forum-disable-2022
    resource: https://communityarchive.victronenergy.com/questions/127972/disable-inverter-in-a-ess-system.html
    title: Victron community archive - Disable inverter in an ESS system (forum, user answer, 2022-04-01)
  - id: forum-block-2026
    resource: https://community.victronenergy.com/t/option-to-block-discharge-but-keep-offgrid-fallback/53597
    title: Victron community - Option to block discharge but keep offgrid fallback (forum, users, 2026-02; concerns a Multi RS Solar)
  - id: interview
    resource: ../project/requirements-decisions.md
    title: Requirements decisions
---

Builds on [victron-mqtt-grid-meter.md](victron-mqtt-grid-meter.md), which covers the virtual grid meter, the topic scheme (`W/<id>/…` with payload `{"value": …}`), keepalive, and the list of grid charging mechanisms. This page answers the two open control questions from [requirements-decisions.md](../project/requirements-decisions.md): how to block discharge, and how to charge from the grid at a chosen power.

# Conclusions

- **No discharge:** write `0` to `W/<id>/settings/0/Settings/CGwacs/MaxDischargePower`; write `-1` to lift the limit.[^ess-mode23][^modbus-attributes] It is the GUI setting "Limit inverter power", which limits "the power being inverted from DC to AC".[^ess-manual] Charging is a separate direction and is not limited by it (inference from that definition; one forum answer agrees[^forum-disable-2022]).
- **It is a persistent setting.** localsettings writes the settings file 2 s after a value changes and skips unchanged values.[^localsettings] A discharge block switched a few times a day is a few flash writes a day; rewriting the same value every control cycle causes none.
- **Charge from the grid at X watts:** write the volatile override `W/<id>/hub4/0/Overrides/Setpoint` (W, positive = import at the grid meter). The setpoint is the grid meter target, not the battery power, so it must be `house load - PV production + X`, recomputed each cycle. Release by writing `null`.[^venus-dbus][^ess-mode23][^systemcalc-dess]
- **Dynamic ESS must be off** (`Settings/DynamicEss/Mode` = 0). In modes 1 and 4 its delegate rewrites all four hub4 overrides every 5 s.[^systemcalc-dess] Scheduled charging must have no active window for the same reason.[^systemcalc-schedule]
- **Not established:** behaviour of `MaxDischargePower = 0` on Venus OS 3.67 in this exact system; whether the hub4 overrides time out when the writer stops; whether a user write to `hub4/0/Overrides/MaxDischargePower` is honoured. hub4control is closed source. All three need a test on the real system.

# Settings that stop discharge

`<id>` is the portal id. Device instance `0` for `settings` and `hub4` is the usual value.

| Setting | Topic | Values | Stored | Effect and caveats |
| --- | --- | --- | --- | --- |
| Limit inverter power | `W/<id>/settings/0/Settings/CGwacs/MaxDischargePower` | W. "-1: No limit on inverter output"; a positive number is the maximum power fed to loads; `0` blocks inverting.[^ess-mode23] Modbus 2704 (int16, scale 0.1, so units of 10 W) and 2720 (int32, W).[^modbus-attributes] | Persistent | "User setting: Max Inverter Power".[^venus-dbus] Documented control point for Mode 2. Limits DC to AC only, so charging from PV surplus and from the grid continues (inference). "MPPT power is excluded" applies to DC-coupled solar chargers, which this system does not have.[^ess-manual] Battery safety mechanisms and sustain override it.[^ess-mode23] |
| Discharge override | `W/<id>/hub4/0/Overrides/MaxDischargePower` | W on the DC side. `-1` = no limit. Victron's own code never writes 0: it writes `max(1.0, pvpower)` to hold the battery idle, "1.0 to allow selling overvoltage".[^systemcalc-dess] | Volatile (hub4 service, not in settings) | "Used by scheduled charging and DynamicEss to limit DC discharge power".[^venus-dbus] Unlike `/Overrides/Setpoint`, the wiki does not say users may write it. With no DC-coupled PV, `1` would be the idle value (inference). Use only after a test. |
| Deprecated percentage | `…/Settings/CGwacs/MaxDischargePercentage` (Modbus 2702) | On/off switch presented as a percentage | Persistent | Marked "Deprecated" in the wiki; replaced by register 2704.[^venus-dbus][^ess-mode23] Do not use. |
| Minimum charge level | `…/Settings/CGwacs/BatteryLife/MinimumSocLimit` | % | Persistent | Raising it to the current charge level stops discharge at that level. Forum users report it works with a margin of about 1 % (anecdote, Multi RS system).[^forum-block-2026] Above the current level it starts a grid recharge, see the linked page. Coarse; needs a write whenever the charge level rises. |
| Scheduled charge window | `…/Settings/CGwacs/BatteryLife/Schedule/Charge/<n>/…` with `AllowDischarge` = 0 | See linked page | Persistent | "If Self-consumption above limit is set to PV, the battery will not be discharged until the scheduled window ends, but available PV will be used for powering loads."[^ess-manual] Runs on the GX without Home Assistant. Below the window's target it force-charges from the grid, so the target must be at or below the current charge level to get a pure block.[^systemcalc-schedule] |
| Mode 3 flags | `W/<id>/vebus/<inst>/Hub4/DisableFeedIn` | 1 = feed-in disabled | Volatile | Only effective in Mode 3. A forum user: "Disable feed-in won't work unless you are running in ESS mode 3, as in Mode 1 or 2 the ESS control loop on the gx will overwrite it."[^forum-disable-2022] |
| Keep batteries charged | `…/Settings/CGwacs/BatteryLife/State` = 9 | | Persistent | Stops discharge but also charges to full from the grid at once.[^modbus-attributes] Not a discharge block. |

Read back the limit that ESS actually applies on `N/<id>/hub4/0/MaxDischargePower` ("Active maximum discharge power limit").[^venus-dbus]

Forum evidence, both anecdote:

- 2022, user answer: "The easiest way I see to do it is to limit inverter power. It is in the ESS menu. I think the register is 2704".[^forum-disable-2022]
- 2026, a user reports that writing 0 to register 2704 had no effect. That system is a Multi RS Solar, which does not run the VE.Bus ESS assistant, so the report does not carry over to a MultiPlus-II (agent's assessment).[^forum-block-2026]

Behaviour during a grid failure with the limit at 0 was not found documented. Because the setting is persistent, a crash of the integration leaves discharge blocked until someone resets it; the integration should restore `-1` on startup and on unload (inference).

# Charging from the grid at a chosen power

| Step | Topic | Value |
| --- | --- | --- |
| Grid setpoint, volatile | `W/<id>/hub4/0/Overrides/Setpoint` | W, int32 (Modbus 2716). Positive imports, negative exports.[^modbus-attributes][^ess-mode23] `null` returns control to the stored setpoint; Victron's code releases it by writing `None`.[^systemcalc-dess] |
| Grid setpoint, persistent | `W/<id>/settings/0/Settings/CGwacs/AcPowerSetPoint` | W (Modbus 2700 int16, 2703 with scale 0.01). The GUI value, default 50 W.[^modbus-attributes][^ess-manual] Leave it alone. |
| Charge power cap | `W/<id>/settings/0/Settings/CGwacs/MaxChargePower` | W. "User setting: Max Charge Power". Persistent.[^venus-dbus] |
| Charge current cap (DVCC) | `W/<id>/settings/0/Settings/SystemSetup/MaxChargeCurrent` | A, int16 (Modbus 2705), replaces the deprecated charge percentage; `0` disables charging. The value for "no limit" was not verified (`-1` by analogy with the discharge limit is an assumption). Persistent.[^modbus-attributes][^ess-mode23] |

Two ways to obtain X watts into the battery (agent's inference from the Mode 2 definition "the system tries to take 100 Watt from the grid. The power will be used to feed the loads or charge the battery"[^ess-mode23]):

1. **Tracking setpoint:** each control cycle write `Overrides/Setpoint = house load - PV production + X`. All inputs are known to the integration. Nothing persistent is touched. The battery charge power follows load changes with the ESS ramp of 400 W per second.
2. **Cap and saturate:** write `MaxChargePower = X` once (it rarely changes; the configured maximum is about 2100 W[^interview]) and set the override to a value safely above load plus X. ESS charges at the cap and imports whatever the load adds. Simpler, but the setpoint must stay below the AC input current limit and the cap is a persistent write.

Variant 1 is preferable for a freely chosen X; variant 2 fits "always charge at maximum charge power", which is the decided behaviour.[^interview]

Discharge block and grid charge combine without conflict: a positive setpoint already means the battery is not discharging, so the block matters only in cheap quarter-hours without a charge slot (inference).

# Is the override really volatile, and does it time out?

- The override lives on the hub4 service, not in settings, so it is not written to the settings file.[^venus-dbus][^localsettings] Victron introduced it for that reason (quoted on the linked page).
- A timeout is not documented. The scheduled charging code contains the comment "keep it the same, but write it to avoid a timeout" for `/Overrides/ForceCharge`, and both Victron delegates rewrite their overrides every 5 s.[^systemcalc-schedule][^systemcalc-dess] This suggests hub4control expires at least the force-charge override; whether the same holds for `Setpoint` and `MaxDischargePower` is not established. Rewriting each cycle (5 to 60 s per house[^interview]) is the safe practice, and a short test (write once, stop, observe) settles it.

# Dynamic ESS and scheduled charging

- The Dynamic ESS delegate runs a 5 s timer only when `/Settings/DynamicEss/Mode` is above 0 (1 = Auto, 4 = Node-RED; Modbus 5423).[^systemcalc-dess][^modbus-attributes] While active it writes `/Overrides/Setpoint`, `/Overrides/ForceCharge`, `/Overrides/MaxDischargePower` and `/Overrides/FeedInExcess`; on deactivation it sets them to `None`, `0`, `-1.0` and `0`.[^systemcalc-dess] An external writer would be overwritten within 5 s. The wiki's wording matches: the setpoint override "can also be used by users when DynamicEss is not used".[^venus-dbus]
- Dynamic ESS does nothing in "Keep batteries charged" or with `Hub4Mode` = 3.[^systemcalc-dess] The manual states it cannot run together with scheduled charging.[^dess-manual]
- The scheduled charging delegate writes `ForceCharge` and `MaxDischargePower` only while a window is active or while it still holds control; with no window it leaves the overrides alone.[^systemcalc-schedule] Keep all five windows disabled if the integration owns the overrides.
- Check before taking control: `N/<id>/settings/0/Settings/DynamicEss/Mode` = 0 and `N/<id>/system/0/DynamicEss/Active` = 0.[^modbus-attributes]

# Relation to victron-mqtt-grid-meter.md

| Statement there | This page |
| --- | --- |
| Override topic `W/<id>/hub4/0/Overrides/Setpoint`, Modbus 2716, int32, W, positive = import | Confirmed.[^modbus-attributes][^ess-mode23] |
| Victron clears the override by writing `None` | Confirmed in the Dynamic ESS code.[^systemcalc-dess] |
| Forum: override is reset to null while Dynamic ESS is on | Confirmed by the source: rewritten every 5 s in modes 1 and 4.[^systemcalc-dess] |
| `ForceCharge` is rewritten every 5 s "when it has control" | Confirmed; additionally a code comment points to a timeout.[^systemcalc-schedule] |
| "Whether `/Overrides/Setpoint` has a timeout" not found | Still not established; indirect evidence only, see above. |
| Charge power cap `MaxChargePower`, DVCC `MaxChargeCurrent` | Confirmed, with Modbus 2705 for the current limit.[^modbus-attributes] |
| `N/<id>/hub4/0/MaxDischargePower` is the active limit | Confirmed; the writable user setting is the separate settings path above.[^venus-dbus] |
| Discharge limit as an option | New here: exact path, value semantics, persistence. No contradiction found. |

# Tests to run on the real system

1. Write `MaxDischargePower = 0` at night with load present: battery power must go to about 0, grid covers the load; then check that PV surplus the next morning charges the battery.
2. With the limit at 0, write `Overrides/Setpoint = load + 1000`: battery must charge at about 1000 W.
3. Write the setpoint override once and stop: note whether and when it reverts.
4. Write `hub4/0/Overrides/MaxDischargePower = 1`: note whether it is honoured and for how long.
5. Restore `-1` and `null` and confirm normal ESS operation.


# Tested on a real GX

Tested on 2026-10-06 on one MultiPlus-II GX with Venus OS v3.67, ESS mode `Hub4Mode` 1, Dynamic ESS off, stored grid setpoint 0 W, the battery discharging about 350 W into the house load at the time. Each override was written once over MQTT as `W/<portal id>/hub4/0/Overrides/<name>` with payload `{"value": ...}` and then not refreshed.[^own-test]

| Test | Result |
| --- | --- |
| `Overrides/Setpoint` = 300 | Grid power went from about 0 W to 300 W import within 30 s and stayed there (median 307 W); the battery discharged correspondingly less. The override still read 300 after 200 s without a refresh |
| `Overrides/Setpoint` = `null` | The override read `null` again and grid power was back near 0 W within 20 s |
| `Overrides/MaxDischargePower` = 0 | Battery discharge fell from about 400 W to about 47 W within 50 s and the grid covered the load (about 450 W import). The override still read 0 after 150 s without a refresh |
| `Overrides/MaxDischargePower` = -1 | The override read `null` again and the battery discharged as before |

What this settles and what it leaves open:

- **Both overrides are honoured for an outside writer** on this system, which the documentation left open.
- **Neither expired** within 200 s and 150 s. Victron's own writers refresh every 5 s, so a timeout, if one existed, would be expected well inside that; treat the overrides as not expiring. Whoever sets one must release it, including after a restart of the controller.
- **A discharge limit of 0 leaves about 47 W of discharge**, presumably the inverter's own consumption. It does not hold the battery at exactly zero.
- **Not tested:** whether PV surplus still charges the battery while the discharge override is 0 (the battery was discharging during the test); a test window longer than 200 s; what ESS does when the grid meter value stops changing or stops arriving, because the meter feed is published by another system.
- **Consequence for safety:** an override that does not expire needs another dead-man. If the controller is also the only publisher of the grid meter value, its death stops that feed, the grid meter driver exits after 60 s, and ESS loses its meter.


[^venus-dbus]: Venus OS wiki - D-Bus services and paths
[^ess-mode23]: Victron - ESS mode 2 and 3
[^modbus-attributes]: dbus_modbustcp attributes.csv (register to D-Bus path mapping, master, fetched 2026-10-04)
[^ess-manual]: Victron ESS manual - Configuration chapter
[^systemcalc-schedule]: dbus-systemcalc-py - delegates/schedule.py (master, fetched 2026-10-04)
[^systemcalc-dess]: dbus-systemcalc-py - delegates/dynamicess.py (master, fetched 2026-10-04)
[^localsettings]: victronenergy/localsettings - localsettings.py (master)
[^dess-manual]: Victron - Dynamic ESS manual (page last modified 2026-09-04)
[^forum-disable-2022]: Victron community archive - Disable inverter in an ESS system (forum, user answer, 2022-04-01)
[^forum-block-2026]: Victron community - Option to block discharge but keep offgrid fallback (forum, users, 2026-02; concerns a Multi RS Solar)
[^interview]: Requirements decisions
[^own-test]: Override test on a MultiPlus-II GX with Venus OS v3.67
