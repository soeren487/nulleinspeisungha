---
type: Overview
title: Overview
description: What nulleinspeisungha is, the installation it controls, and where the project stands.
tags: [project]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T22:10:00Z }
sources:
  - id: initial-requirements
    resource: ../../raw/2026-10-04-initial-requirements.md
    title: Initial requirements stated by Soeren
    author: human:soeren
  - id: repo-state
    resource: git repository at /home/soeren/ai/nulleinspeisungha
    title: Repository state on 2026-10-04
---

# Purpose

A Home Assistant custom integration that applies a zero feed-in strategy to Soeren's photovoltaic and battery installation, for several houses from one Home Assistant instance.[^initial-requirements]

It has two jobs per house:[^initial-requirements]

1. **Limit feed-in.** Keep grid feed-in at or below a configurable setpoint by setting the power limit of the house's inverters, at a configurable update interval.
2. **Charge from the grid when cheap.** Take over the existing Node-RED strategy that charges the AC battery from the grid when Tibber rates the price as cheap, aiming for a full battery by the end of the coming night.

It also has to detect an OpenDTU that has stopped exchanging data with its inverters and restart it automatically.[^initial-requirements]

# Installation

| Per house | Equipment |
| --- | --- |
| Grid measurement | Shelly Pro 3EM at the grid connection |
| AC battery | Victron MultiPlus-II GX, read over MQTT, fed a grid power value through a virtual MQTT grid meter; charger power about 2100 W |
| Electricity tariff | Tibber, dynamic 15-minute prices |

Shared across both houses:[^initial-requirements]

- 12 Hoymiles inverters, reached through 3 OpenDTU devices over their REST API. OpenDTUs are connected globally; each inverter is then assigned to a house.
- 2 of the inverters sit behind DC batteries (four Anker Solix E1600) fed directly by the panels. They deliver power only once the batteries are full, or at night during the release window. Installation details (addresses, serial numbers) are kept in the private part of the wiki, outside the repository.

# State

- Requirements are settled: see [Requirements decisions](requirements-decisions.md). The spec is `.scratch/nulleinspeisung/spec.md`; tickets live under `.scratch/nulleinspeisung/issues/`, each with its status. As of 2026-10-24 all planned features are built and released up to 0.7.0 (curtailment with battery priority, stuck-DTU restart, grid power published to both Victron systems, grid charging with efficiency check and discharge block, PV forecast, expected load, DC battery support); ticket 31 (usable energy above the Victron's minimum charge level) is released as 0.7.1, and ticket 32 (grid charging planned against shortfalls in expensive periods) was released in 0.8.0 and ticket 33 (a daily charge to 95 % so that BatteryLife lowers its minimum) in 0.9.0; ticket 34 (safety buffer against a forecast that does not come true) is merged but not released. Open: 16 (one observed night per house), 23 (on hold for a firmware update) and 30 (fewer limit commands when the battery is full). The integration runs on the owner's production Home Assistant and his Node-RED flow is off since 2026-10-06. The repository is public on GitHub (`soeren487/nulleinspeisungha`), so nothing secret or private may be committed.[^repo-state]

# Related

- [Development workflow](development-workflow.md)

[^initial-requirements]: Initial requirements stated by Soeren
[^repo-state]: Repository and installation state, last updated 2026-10-24
