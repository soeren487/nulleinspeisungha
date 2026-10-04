---
type: Research Finding
title: OpenDTU and Hoymiles limit control
description: How to read inverters and set power limits through OpenDTU's REST API, the timing and failure behaviour that constrain a zero feed-in loop, and how existing controllers do it.
tags: [opendtu, hoymiles, rest-api, power-limit, zero-feed-in, failure-detection]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T08:56:35Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: own-measurement
    resource: measurement on the owner's installation, 2026-10-05
    title: Limit response measured on one HM-1200-4T
  - id: opendtu-src
    resource: https://github.com/tbnobody/OpenDTU/tree/8da432d52f33a7598deb93550df2115b8e5bb700
    title: tbnobody/OpenDTU source at 8da432d (tag v26.9.28), src/WebApi_*.cpp and lib/Hoymiles
    last_modified: 2026-09-28T16:00:48Z
  - id: opendtu-docs
    resource: https://www.opendtu.solar/firmware/web_api/
    title: OpenDTU documentation (tbnobody/OpenDTU-Docs) - Web API, FAQ, DTU settings, inverter settings, MQTT topics, troubleshooting
    last_modified: 2026-09-24T19:04:06Z
  - id: opendtu-limit-type-change
    resource: https://github.com/tbnobody/OpenDTU/commit/8cab3335f348d9ec5221ebf01634c593e3a4213b
    title: "Commit: BREAKING CHANGE, /api/limit/config limit_type renumbered 0..3 (first tag v25.9.11)"
    last_modified: 2025-08-07T00:00:00Z
  - id: opendtu-queue-change
    resource: https://github.com/tbnobody/OpenDTU/commit/8acae28c
    title: "Commit: New handling of command queue (first tag v25.1.14)"
    last_modified: 2024-12-15T00:00:00Z
  - id: opendtu-early-exit
    resource: https://github.com/tbnobody/OpenDTU/commit/143318432ee70eb733bf3184466b60dbe7d53190
    title: "Commit: early exit on completed radio transactions (in v26.9.28)"
    last_modified: 2026-09-21T00:00:00Z
  - id: opendtu-i836
    resource: https://github.com/tbnobody/OpenDTU/issues/836
    title: "Issue 836: OpenDTU slowing down or stopping (maintainer comments, 2023)"
  - id: opendtu-i1147
    resource: https://github.com/tbnobody/OpenDTU/issues/1147
    title: "Issue 1147: request for ESP restart via MQTT (open)"
  - id: opendtu-i2040
    resource: https://github.com/tbnobody/OpenDTU/issues/2040
    title: "Issue 2040: no connection to HMS-1600-4T in the morning (closed 2025-02)"
  - id: opendtu-i2137
    resource: https://github.com/tbnobody/OpenDTU/issues/2137
    title: "Issue 2137: lost connection to HMS-1600-4T, frequency hopping"
  - id: opendtu-i2641
    resource: https://github.com/tbnobody/OpenDTU/issues/2641
    title: "Issue 2641: permanent limit is not permanent anymore (HMS firmware 2.0.4)"
  - id: opendtu-i3250
    resource: https://github.com/tbnobody/OpenDTU/issues/3250
    title: "Issue 3250: NRF communication problems with HM inverters on v26.9.28 (open, 2026-09-29)"
  - id: opendtu-i35
    resource: https://github.com/tbnobody/OpenDTU/issues/35
    title: "Issue 35: Leistungsreduzierung HM300 (2022, origin of the limit feature)"
  - id: opendtu-pr3227
    resource: https://github.com/tbnobody/OpenDTU/pull/3227
    title: "PR 3227: native OpenDTU Zero-Export controller (open, 2026-09, not merged)"
  - id: onbattery-src
    resource: https://github.com/hoylabs/OpenDTU-OnBattery/tree/6aada2b5ca3dd222ff5e9207339f209cedc3642e
    title: hoylabs/OpenDTU-OnBattery source at 6aada2b, src/PowerLimiter*.cpp (dynamic power limiter)
    last_modified: 2026-10-01T16:28:41Z
  - id: onbattery-docs
    resource: https://github.com/hoylabs/OpenDTU-OnBattery-Docs/blob/main/docs/firmware/configuration/dpl.md
    title: OpenDTU-OnBattery documentation, dynamic power limiter (dpl.md, configuration/dpl.md)
    last_modified: 2026-10-01T16:22:36Z
  - id: onbattery-pr901
    resource: https://github.com/hoylabs/OpenDTU-OnBattery/pull/901
    title: "PR 901: restart unresponsive inverter (merged 2024-04)"
  - id: hzx-src
    resource: https://github.com/reserve85/HoymilesZeroExport/tree/ad6f92dfcdd9c5f0fc4e00e80b3b568c19a81660
    title: reserve85/HoymilesZeroExport v1.102, HoymilesZeroExport.py and default config
    last_modified: 2026-08-24T19:09:54Z
  - id: hzx-i211
    resource: https://github.com/reserve85/HoymilesZeroExport/issues/211
    title: "Issue 211: OpenDTU hangs in the morning when driven by HoymilesZeroExport (user reports)"
  - id: ahoy-manual
    resource: https://github.com/lumapu/ahoy/blob/main/manual/User_Manual.md
    title: AhoyDTU user manual (sister project, same inverter protocol)
  - id: forum-eeprom
    resource: https://akkudoktor.net/t/eeprom-lebensdauer-bei-nulleinspeisung/38097
    title: "Forum thread: EEPROM lifetime with zero feed-in (anecdotal)"
---

# Conclusions

- **Limit type**: use *relative non-persistent* (`limit_type: 1`) or *absolute non-persistent* (`0`) in the loop. Never cycle a persistent type. Values 0 and 1 mean the same in every OpenDTU version; the persistent values changed from 256/257 to 2/3 in v25.9.11.[^opendtu-limit-type-change]
- **Persistent limit**: set once, low, as the start-up and fail-safe value. The inverter falls back to it whenever it restarts (every morning for panel-fed inverters).[^opendtu-docs][^ahoy-manual]
- **Data freshness**: OpenDTU polls **one inverter per poll interval** (default 5 s), round robin. With 4 inverters on a DTU each inverter is refreshed about every 20 s.[^opendtu-src]
- **Limit acknowledgement**: about 100-150 ms per command on v26.9.28 and later, up to 2 s per command before that. An unreachable inverter blocks the radio for roughly 10 s per attempt.[^opendtu-early-exit][^opendtu-src]
- **Loop interval**: existing controllers default to 20-30 s; 5 s is the tested lower bound of the newest one. No hard limit from Hoymiles was found.[^hzx-src][^opendtu-pr3227]
- **`limit_set_status: "Ok"` means the inverter acknowledged the radio frame, not that output changed.** While a loop sends limits more often than every 4 minutes, `limit_relative` is only an echo of the last acknowledged command.[^opendtu-src][^opendtu-i2641]
- **Stuck DTU**: no single documented root cause. Detection has to combine `data_age`, `reachable`, `radio_stats` counters, the `hints` block and knowledge of whether production is expected. See the proposal in section 6.
- **Version risk**: v26.9.28 has an open regression report for NRF24 (HM series) communication.[^opendtu-i3250]

Not found in any reliable source: a Hoymiles-published maximum limit-change rate, a measured time from acknowledgement to changed AC output, and the EEPROM write endurance of the inverters.

# 1. REST API

All paths are served on port 80. Verified against source at tag v26.9.28.[^opendtu-src]

**Auth.** HTTP Basic, user is always `admin`, default password `openDTU42`. Every POST and the configuration GETs always require it. Status GETs are open while "allow read-only access" is on (default on); if it is off, every endpoint needs auth.[^opendtu-src][^opendtu-docs]

**POST format.** Form-encoded body with one field `data` holding a JSON string: `data={"serial":"1164...","limit_type":1,"limit_value":50}`. A raw JSON body is rejected with `No values found!`. Replies carry `type` (`success` or `warning`), `message`, `code`.[^opendtu-src][^opendtu-docs]

| Purpose | Method and path | Auth | Request | Important response fields |
| --- | --- | --- | --- | --- |
| Live data, all inverters | `GET /api/livedata/status` | read-only | none | `inverters[]`: `serial`, `name`, `data_age`, `data_age_ms`, `poll_enabled`, `reachable`, `producing`, `limit_relative`, `limit_absolute` (-1 if max power unknown), `radio_stats{tx_request, tx_re_request, rx_success, rx_fail_nothing, rx_fail_partial, rx_fail_corrupt, rssi}`; `total.Power/YieldDay/YieldTotal`; `hints{time_sync, radio_problem, default_password, pin_mapping_issue}`. **No per-inverter power in this form.** |
| Live data, one inverter | `GET /api/livedata/status?inv=<serial>` | read-only | none | The same common fields plus `AC`, `DC`, `INV` channel objects, each value as `{v, u, d}`; AC power is `inverters[0].AC["0"].Power.v`. |
| Inverter list (configuration) | `GET /api/inverter/list` | always | none | `inverter[]`: `id`, `serial`, `name`, `type`, `poll_enable`, `poll_enable_night`, `command_enable`, `command_enable_night`, `reachable_threshold`, `zero_runtime`, `channel[]` |
| Device info | `GET /api/devinfo/status?inv=<serial>` | read-only | none | `max_power`, `hw_model_name`, `fw_build_version`, `pdl_supported` |
| Limit status | `GET /api/limit/status` | read-only | none | per serial: `limit_relative`, `max_power`, `limit_set_status` |
| Set limit | `POST /api/limit/config` | always | `serial` (string), `limit_type` (0-3), `limit_value` (number, max 2250) | `type`, `message` |
| Power command status | `GET /api/power/status` | read-only | none | per serial: `power_set_status` |
| Inverter on / off / restart | `POST /api/power/config` | always | `serial` plus either `power: true/false` or `restart: true` | `type`, `message` |
| Reboot the DTU | `POST /api/maintenance/reboot` | always | `{"reboot": true}` | `type: success`, `message: Reboot triggered!` |
| DTU health | `GET /api/system/status` | read-only | none | `uptime`, `git_hash` (version), `resetreason_0`, `heap_*`, `nrf_connected`, `cmt_connected` |
| DTU settings | `GET` / `POST /api/dtu/config` | always | full object on POST | `pollinterval`, `nrf_palevel`, `cmt_palevel`, `cmt_frequency` |
| Event log | `GET /api/eventlog/status?inv=<serial>` | read-only | none | `events[]` with `message_id`, `message` |

Notes:

- The serial is parsed as hex text; send it as a string exactly as shown in the UI.[^opendtu-src]
- Under memory pressure `/api/livedata/status` answers `429 Too Many Requests` with `Retry-After: 60`.[^opendtu-src]
- One DTU holds at most 10 inverters (`INV_MAX_COUNT`).[^opendtu-src]
- The docs warn that the Web API exists for the web UI, is used at one's own risk, and may break compatibility.[^opendtu-docs]

**Version changes that matter**

| Version | Change |
| --- | --- |
| v25.9.11 (2025-08) | `limit_type` renumbered: persistent absolute 256 -> 2, persistent relative 257 -> 3. Non-persistent 0 and 1 unchanged. Sending 256/257 now returns `Invalid type specified!`.[^opendtu-limit-type-change] |
| v25.1.14 (2024-12) | New command queue: a queued limit command for an inverter is replaced by a newer one instead of piling up.[^opendtu-queue-change] |
| 2025-03 | `data_age_ms` added. `radio_stats` exists since 2024-09.[^opendtu-src] |
| v26.9.28 (2026-09) | Radio transaction ends as soon as the answer is complete ("early exit").[^opendtu-early-exit] |
| v24.2.12 | Minimum version demanded by HoymilesZeroExport for the API shape above.[^hzx-src] |

**WebSocket.** `ws://<host>/livedata` pushes a JSON message per inverter (same structure as the `?inv=` form, including channels, `total` and `hints`) when new statistics arrived, and at least every 10 s; the check runs once per second. It needs HTTP Digest auth only when read-only access is disabled.[^opendtu-src] Compared with polling it removes one request per inverter and delivers data as soon as it is received, but it carries no `limit_set_status`; that still needs `GET /api/limit/status`. Own assessment: polling is simpler and sufficient because data cannot be fresher than the radio poll cycle anyway.

# 2. Limit types

| `limit_type` | Name in source | Meaning |
| --- | --- | --- |
| 0 | AbsolutNonPersistent | watts, held in inverter RAM |
| 1 | RelativNonPersistent | percent of rated power, held in inverter RAM |
| 2 | AbsolutPersistent | watts, stored in the inverter |
| 3 | RelativPersistent | percent, stored in the inverter |

- Relative values above 100 are clamped to 100. The API accepts up to 2250 for any type.[^opendtu-src]
- A non-persistent limit is lost when the inverter loses DC power or restarts; it then applies the last persistent value.[^opendtu-docs][^ahoy-manual]
- With an absolute type OpenDTU can only update its displayed limit if the inverter's `max_power` is known; otherwise the display lags until the periodic read-back (about 4 minutes).[^opendtu-src][^opendtu-docs]
- **Loop choice**: OpenDTU-OnBattery and HoymilesZeroExport send relative non-persistent; the proposed native controller sends absolute non-persistent and never touches the persistent limit.[^onbattery-src][^hzx-src][^opendtu-pr3227]

**Wear.** No Hoymiles statement and no endurance figure was found. The risk is stated by the sister project: the AhoyDTU manual says the persistent limit "should not be modified cyclic by a script because of potential wearout of the flash inside the inverter" and that non-persistent limits do "not damage the EEPROM".[^ahoy-manual] The concern was raised by users when the OpenDTU feature was designed.[^opendtu-i35] The "100 000 cycles" figure circulating in forums is a generic EEPROM number, not a Hoymiles one (anecdotal).[^forum-eeprom] OpenDTU's own docs carry no wear warning.[^opendtu-docs]

PR 3227 mentions an inverter-side "non-persistent-limit timeout" that reverts the limit by itself.[^opendtu-pr3227] No other source confirms this; treat it as unverified.

# 3. How often the limit can be changed

**OpenDTU side** (from source):[^opendtu-src]

- The API applies no rate limit and always answers `success` once the command is queued.
- Each radio (NRF24, CMT2300A) has one FIFO queue and handles one command at a time. Polling and limit commands share it.
- A new limit for an inverter replaces a still-queued limit of the same type for that inverter.[^opendtu-queue-change]
- A limit command waits up to 2 s for the acknowledgement. Since v26.9.28 it finishes on receipt, typically 100-150 ms.[^opendtu-early-exit]
- No answer: the command is re-sent up to 5 times in total, 2 s each, so about 10 s of radio time, then the status becomes `Failure`. OpenDTU then re-queues the same limit every time that inverter's poll turn comes, without end.
- Commands are dropped silently if "Send commands" is off for the inverter, or at night if "Send commands at night" is off. The API still answers `success`.[^opendtu-src][^opendtu-docs]

**Statements from maintainers and contributors**

- tbnobody (owner): the event log entry "DTU command failed" most likely appears when "you are setting a limit too often", the DTU reboots often, or a second DTU talks to the inverter.[^opendtu-i836]
- stefan123t (contributor): setting the limit too frequently "can prevent the inverter from responding to any other requests for some 15 or even more seconds"; queued limit commands to an offline inverter can keep OpenDTU from normal queries.[^opendtu-i2137] This is a contributor's observation, not a measurement.
- OpenDTU must not read the limit back within 4 minutes of a limit command, or the inverter logs errors.[^opendtu-src][^opendtu-docs]

**Effect of inverter count.** With N inverters on one radio, statistics refresh every N x poll interval, and limit commands for all N are sent one after another. PR 3227 reports a regulation cycle across 8 HMS inverters taking about 2 s with early exit and about 16 s without.[^opendtu-pr3227]

**Practice.** Default intervals: 20 s (HoymilesZeroExport), 30 s with a 5 s minimum (PR 3227). OpenDTU-OnBattery says its limiter "can be slow (>10 seconds)" and with tuning reach "one or two seconds"; it runs inside the DTU and has no HTTP round trips.[^hzx-src][^opendtu-pr3227][^onbattery-docs]

# 4. Applying and confirming a limit

**`limit_set_status` values**: `Ok`, `Pending`, `Failure` (plus `Unknown` as fallback). It starts as `Ok` after boot.[^opendtu-src]

| Step | What happens |
| --- | --- |
| POST accepted | status `Pending` |
| Inverter acknowledges | status `Ok`; `limit_relative` is set to the value that was *sent* |
| No acknowledgement | status `Failure`; automatic re-send on later poll turns |
| 2-4 minutes without further limit commands | OpenDTU reads the real limit from the inverter and overwrites `limit_relative` |

Source for all four rows:[^opendtu-src] tbnobody confirms: on acknowledgement the ESP "*assumes* the limit was applied".[^opendtu-i2641] At boot `limit_relative` is 0 until the first read-back, at least 4 minutes later.[^opendtu-docs]

**Time to effect.** No authoritative figure. tbnobody (2022): `Pending` returns to `Ok` quickly, "<4 Sek".[^opendtu-i35] The only hard confirmation that output changed is a statistics sample newer than the acknowledgement, which arrives up to N x poll interval later. OpenDTU-OnBattery waits for exactly that before it calculates again.[^onbattery-src]

**Series differences**

| Series | Radio | Notes |
| --- | --- | --- |
| HM | NRF24L01+ (2.4 GHz) | Limit always split per input. Limit range 2-100 % according to the AhoyDTU manual.[^opendtu-src][^ahoy-manual] |
| HMS, HMT | CMT2300A (868 MHz) | After a restart or 15 minutes without communication the inverter listens on 865 MHz and must be re-tuned by the DTU.[^opendtu-docs] Firmware 2.0.4 changed the limit read-back format; support was added in 2025-08.[^opendtu-i2641][^opendtu-src] |
| HMS-xxxW (built-in Wi-Fi) | none usable | Not supported by OpenDTU.[^opendtu-docs] |

No source compares apply times between series.

**Multi-input behaviour.** The limit is divided equally among the inputs, so a shaded input wastes its share: a 4-input inverter limited to 600 W gives each input 150 W.[^onbattery-src][^onbattery-docs] Exception: HMS 4-input models with inverter firmware 01.01.12 or newer limit the AC output instead ("power distribution logic"); OpenDTU reports this as `pdl_supported`.[^opendtu-src] OpenDTU-OnBattery compensates on other models by raising the limit when it detects inputs producing below their share ("overscaling").[^onbattery-src]

**Minimum limit**

| Source | Value |
| --- | --- |
| OpenDTU-OnBattery docs | "Small power limits can lead to oscillating power output and the inverter shutting down"; rule of thumb: at least inputs x 12 W. Default lower limit 10 W.[^onbattery-docs][^onbattery-src] |
| PR 3227 | 40 W per inverter, default and minimum[^opendtu-pr3227] |
| HoymilesZeroExport | 5 % of rated power by default[^hzx-src] |
| AhoyDTU manual | 2 % is the lower end of the relative range for HM[^ahoy-manual] |

# 5. Data freshness

- Every poll interval OpenDTU requests statistics from **one** inverter and moves to the next. Default 5 s. Set under Settings -> DTU or `POST /api/dtu/config` (`pollinterval`, must be greater than 0).[^opendtu-src]
- The docs warn that too small a value starves the ESP.[^opendtu-docs] In 2023 a 1 s interval overflowed the command queue at night.[^opendtu-i836]
- OpenDTU sends no inverter requests until it has a valid clock (NTP). `hints.time_sync: true` means the clock is **not** set.[^opendtu-src]

| Field | Meaning |
| --- | --- |
| `data_age` / `data_age_ms` | time since the last complete statistics answer. Before the first answer after boot it equals the uptime. |
| `reachable` | polling enabled **and** consecutive failed statistics requests <= `reachable_threshold` (default 2). Turns false after about 3 x N x poll interval. |
| `producing` | polling enabled and AC power > 0 |
| `poll_enabled` | false at night unless "Poll inverter data at night" is on; day and night come from OpenDTU's own sun calculation |
| `radio_stats.*` | counters per inverter, reset at midnight |

Source for the table:[^opendtu-src] With `zero_runtime` enabled, power values are zeroed once an inverter is unreachable; otherwise the last values stay.[^opendtu-docs]

For the 4 battery-fed inverters that deliver at night, "Poll inverter data at night" and "Send commands at night" must be on (own conclusion from the settings above).

# 6. The "stuck until reboot" failure

**What is documented or reported**

| Cause or symptom | Evidence |
| --- | --- |
| Limit commands to an unreachable inverter filled the queue and blocked polling; typical sign was inverters staying offline in the morning under a zero feed-in script. Maintainer considers it fixed by the queue change in v25.1.14. | [^opendtu-i2040][^opendtu-queue-change][^hzx-i211] |
| HMS/HMT on another frequency than the DTU; inverter shown offline while producing. One report: a DTU reboot did **not** help, changing the frequency did. | [^opendtu-i2137] |
| Weak supply or radio module problems; NRF24 needs a stable supply. | [^opendtu-docs] |
| Two DTUs querying the same inverter. | [^opendtu-docs] |
| Regression: HM inverters lose packets on v26.9.28, fine on v26.3.30 (open). | [^opendtu-i3250] |
| Users restarting OpenDTU daily or wanting a remote restart; the maintainer regards a needed reboot as a bug to fix rather than automate. | [^opendtu-i1147] |

No confirmed, current root cause for a radio that stays dead until reboot was found. The reports are user observations.

**Does the reboot endpoint still work?** Not documented. From source: the handler only needs the web server and the main scheduler loop, and restarts the ESP about one second later.[^opendtu-src] It should therefore work when the radio is stuck but HTTP answers, and cannot work when the ESP is hung or off the network. One user notes the reset page was only sometimes reachable in the degraded state.[^opendtu-i836] OpenDTU-OnBattery uses the same restart path as its last resort.[^onbattery-src]

**Proposal (own, not from a source)**

Let T = number of polled inverters on the DTU x poll interval.

| Observation | Interpretation |
| --- | --- |
| HTTP request fails or times out 3 times in a row | DTU down or off the network. API reboot impossible; alert, or power-cycle through a switchable socket. |
| `uptime` lower than at the previous sample | DTU rebooted by itself; start a grace period. |
| `hints.radio_problem: true` | Radio chip not answering. Reboot candidate at once. |
| `hints.time_sync: true` for more than 5 minutes | No clock, so no polling. A reboot helps only if NTP is reachable. |
| Some inverters on the DTU reachable, one not | Radio range or that inverter. Not a DTU fault; do not reboot. |
| All inverters unreachable, `poll_enabled: false` | Night as seen by OpenDTU. Normal. |
| All `poll_enabled` inverters unreachable, sun is down, none battery-fed | Normal. |
| All `poll_enabled` inverters have `data_age` > max(3 x T, 60 s) for 10 minutes while production is expected | DTU suspected stuck. Reboot. |
| As above and `radio_stats.tx_request` does not increase between samples | Poll loop itself stalled; stronger evidence. |
| `limit_set_status` stays `Pending` or `Failure` for more than 60 s on a `reachable` inverter | Command path stuck; count it, reboot after several inverters or repeats. |

"Production is expected" should come from evidence outside the DTU: sun elevation above about 5 degrees for panel-fed inverters, inverters on another DTU of the same house producing, or the battery release window for the 4 battery-fed inverters.

After a reboot: wait at least 5 minutes before judging again (limit read-back takes 4 minutes, HMS re-tuning can take up to 15), allow at most about 3 automatic reboots per day per DTU, then stop and notify. All thresholds are starting values to be tuned on the real installation.

# 7. How existing controllers structure the loop

| Aspect | OpenDTU-OnBattery limiter[^onbattery-src][^onbattery-docs] | HoymilesZeroExport[^hzx-src] | OpenDTU PR 3227 (unmerged)[^opendtu-pr3227] |
| --- | --- | --- | --- |
| Trigger | Event driven: needs inverter statistics newer than the last command **and** a meter reading newer than those; backoff 128 ms growing to 1 s when stable | Fixed loop, default 20 s; meter polled every 1 s for fast reactions | Fixed interval, default 30 s, minimum 5 s |
| Calculation | target = current inverter output + meter reading - target consumption | new setpoint = previous setpoint + meter reading - target | new limit = previous requested limit + grid error |
| Damping | Hysteresis in watts (default 0; contributor mentions 30 W in use[^opendtu-i2137]) | Tolerance band +/-25 W around a target of -75 W; large reductions approached in steps (20 %); jump to 100 % when import exceeds a threshold | Upward corrections spread over the meter's update period; downward immediate; limit capped near current production |
| Distribution | Inverters sorted by how much each can contribute; as few as possible are changed | Proportional to each inverter's usable range, with priorities for battery inverters | Proportional to rated power |
| Lower bound | Per-inverter minimum; solar inverters are held there, battery inverters switched off | Per-inverter minimum percent | 40 W per inverter |
| Unreachable inverter | Skipped; its last reported output is still assumed to be flowing | Skipped while `reachable` is false | No command queued; others absorb the correction |
| No confirmation | 30 s timeout per update; failed command is not retried, a fresh limit is computed. 10 consecutive failures: restart command to the inverter. 20: reboot the DTU.[^onbattery-pr901] | Polls `limit_set_status` every 0.5 s for up to 10 s; on timeout re-sends on the next loop. Re-sends if the DTU-reported limit drifts more than 5 % of rated power from the commanded one. | Does not use the read-back limit as feedback, because it "may be delayed or stale" |
| Meter failure | Falls back to a configured base load | Optional: set inverters to minimum | Fail-safe limit after a timeout |

Common ground: non-persistent limits only, exclusive control of the governed inverters, never command an unreachable inverter, and wait for the effect of one change before computing the next.


# Measured on a real inverter

Measured on 2026-10-05 at night on one HM-1200-4T (inverter firmware build 2020-06-24) behind a DC battery delivering a steady 290 W, through OpenDTU v26.3.30, by setting non-persistent limits and reading the AC power every 1.5 s. One inverter, one night: treat the numbers as indicative.[^own-measurement]

| What | Result |
| --- | --- |
| Acknowledgement (`limit_set_status` from `Pending` to `Ok`) | 2 to 5 s after the POST |
| Relative non-persistent limit (type 1), lowering 100 % to 10 % | No change in output for about 155 s, then a ramp down over about 30 s to the target. A first attempt watched for only 75 s saw no effect at all |
| Relative non-persistent limit, raising to 100 % | Output started to rise within about 6 s |
| Absolute non-persistent limit (type 0), lowering | Output started to fall 6 to 10 s after the POST |
| Absolute non-persistent limit, raising | Output started to rise within about 7 s |
| Ramp, both directions | About 6 W per second, which is 0.5 % of rated power per second: 430 W to 155 W took about 47 s |
| Reported limit | After an absolute limit OpenDTU reports both forms, for example 99.6 W and 8.3 % |
| Returning to full | Absolute limit equal to the rated power is reported as 100 % |

Conclusions for the control loop:

- **Use absolute non-persistent limits.** Lowering with a relative limit was delayed by minutes; with an absolute limit it started within seconds. The integration computes watts from its percent and the inverter's rated power.
- **The output follows a ramp, not a step.** A change of several hundred watts on one inverter takes most of a minute. A loop that runs every 15 s and reacts to the full remaining deviation each time will overshoot unless it accounts for the change still in flight.
- **Side effect behind a DC battery:** after the limit was released the inverter overshot to about 580 W for roughly two minutes before settling back to 295 W.

Not measured: an HM-600 or HM-1500 fed by panels, and the HMS-1600 (different radio).


[^opendtu-src]: tbnobody/OpenDTU source at 8da432d (tag v26.9.28)
[^opendtu-docs]: OpenDTU documentation (opendtu.solar)
[^opendtu-limit-type-change]: OpenDTU commit 8cab333, limit_type renumbered
[^opendtu-queue-change]: OpenDTU commit 8acae28c, new handling of command queue
[^opendtu-early-exit]: OpenDTU commit 1433184, early exit on completed radio transactions
[^opendtu-i836]: OpenDTU issue 836
[^opendtu-i1147]: OpenDTU issue 1147
[^opendtu-i2040]: OpenDTU issue 2040
[^opendtu-i2137]: OpenDTU issue 2137
[^opendtu-i2641]: OpenDTU issue 2641
[^opendtu-i3250]: OpenDTU issue 3250
[^opendtu-i35]: OpenDTU issue 35
[^opendtu-pr3227]: OpenDTU pull request 3227 (unmerged)
[^onbattery-src]: hoylabs/OpenDTU-OnBattery source at 6aada2b
[^onbattery-docs]: OpenDTU-OnBattery documentation, dynamic power limiter
[^onbattery-pr901]: OpenDTU-OnBattery pull request 901
[^hzx-src]: reserve85/HoymilesZeroExport v1.102
[^hzx-i211]: HoymilesZeroExport issue 211 (user reports)
[^ahoy-manual]: AhoyDTU user manual
[^forum-eeprom]: akkudoktor.net forum thread (anecdotal)
[^own-measurement]: Limit response measured on one HM-1200-4T
