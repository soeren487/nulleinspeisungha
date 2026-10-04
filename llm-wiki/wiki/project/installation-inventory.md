---
type: Reference
title: Installation inventory
description: The three DTUs and twelve inverters as read from the OpenDTU APIs, with models, rated power and observations that affect the design.
tags: [project, hardware, opendtu, hoymiles]
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T19:30:00Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: dtu-api
    resource: GET /api/livedata/status, /api/system/status, /api/limit/status, /api/devinfo/status on the three OpenDTUs
    title: OpenDTU API read-out on 2026-10-04 around 10:10 UTC
  - id: soeren-answers
    resource: answers from Soeren in the requirements interview, 2026-10-04
    title: Soeren's interview answers
    author: human:soeren
---

# DTUs

All three run OpenDTU `v26.3.30`. Read endpoints answer without authentication; writes need the admin password.[^dtu-api]

| DTU | URL | Board | Inverters |
| --- | --- | --- | --- |
| Garage | `https://opendtu-garage.slcl.eu` | ESP32 | 6 |
| Stall | `https://opendtu-stall.slcl.eu` | ESP32 | 3 |
| Büro | `https://opendtu-buero.slcl.eu` | ESP32-S3 | 3 |

# Inverters

| DTU | Name | Serial | Model | Rated W |
| --- | --- | --- | --- | --- |
| Garage | Büro 1 | 114163905599 | HM-600-2T | 600 |
| Garage | Büro 2 | 114163209377 | HM-600-2T | 600 |
| Garage | Büro 3 | 116164603551 | HM-1200-4T | 1200 |
| Garage | Haus 1 | 114163209149 | HM-600-2T | 600 |
| Garage | Haus 2 | 114163906326 | HM-600-2T | 600 |
| Garage | Haus 3 | 116164603614 | HM-1200-4T (by serial family and Soeren's statement; not read back) | 1200 |
| Stall | Offenstall Vorne | 116183068031 | HM-1500-4T | 1500 |
| Stall | Offenstall Mitte | 116183070391 | HM-1500-4T | 1500 |
| Stall | Offenstall Hinten | 116183068028 | HM-1500-4T | 1500 |
| Büro | OmaOpa | 114183178036 | HM-600-2T | 600 |
| Büro | Büro 4 | 116191100801 | HM-1500-4T | 1500 |
| Büro | Büro 5 | 1164a00ccd81 | HMS-1600-4T | 1600 |

All inverters on Stall belong to the living house, all on Büro to the office and workshop house; Garage serves inverters of both houses.[^soeren-answers] The per-inverter assignment on Garage is not recorded yet.

# DC batteries

Four Anker Solix E1600 feed two HM-1200 inverters.[^soeren-answers] "Büro 3" and "Haus 3" on Garage are those two, one per house.[^soeren-answers] They deliver a fixed 300 W from 21:00 to 06:00 and are otherwise off until the batteries are full.[^soeren-answers]

# Observations that affect the design

- **Offline inverters slow their DTU.** On Garage, with two inverters unreachable, the reachable ones showed data ages of 12 to 46 s; on the other two DTUs data ages were 0 to 10 s.[^dtu-api] This matches the radio blocking described in [OpenDTU and Hoymiles limit control](../research/opendtu-hoymiles-limit-control.md).
- **All three DTUs had an uptime of 130 to 190 s** at read-out, because Soeren had restarted them by hand just before; nothing restarts them on a schedule.[^dtu-api][^soeren-answers]
- **After a restart the reported limit is 0 %** with status "Ok" until the DTU has read it back, so a reported limit cannot be trusted shortly after a DTU restart.[^dtu-api]
- **Per-inverter power needs one request each.** The summary `GET /api/livedata/status` carries no power values; AC power is only in `?inv=<serial>`. An unknown serial answers `{"inverters": []}`. Reads need no authentication; `/api/dtu/config` and `/api/inverter/list` answer HTTP 401 with an empty body without the admin password.[^dtu-api]
- **At night** the DTU sets `poll_enabled` to false, reports every inverter as unreachable and lets `data_age` grow; AC values are still present, as zeros.[^dtu-api]
- The HMS-1600-4T uses the CMT radio, the HM models the NRF24 radio, so the Büro DTU carries both.

[^dtu-api]: OpenDTU API read-out on 2026-10-04 around 10:10 UTC
[^soeren-answers]: Soeren's interview answers
