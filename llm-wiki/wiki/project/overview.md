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

- Requirements are settled: see [Requirements decisions](requirements-decisions.md). The spec is `.scratch/nulleinspeisung/spec.md` with 19 tickets under `.scratch/nulleinspeisung/issues/`; each ticket file carries its status. Tickets 01 to 09, 11, 13, 17, 18, 21, 22 and 24 are done and ticket 12 is built as of 2026-10-06: the integration installs, connects DTUs, restarts a stuck DTU, has Houses with a grid meter and assigned inverters, can curtail a House's inverters to the feed-in setpoint, fetches Tibber quarter-hour prices per House, and learns a PV forecast per House from Open-Meteo irradiance and its own production history. Curtailment reads the AC battery over a direct MQTT connection to each GX and gives it priority. Soeren installed the state after ticket 05 on the production Home Assistant and added DTUs and Houses successfully. Limits have been sent to real inverters only in measurement sessions; Curtailment itself has not run on the real system. The repository is public on GitHub (`soeren487/nulleinspeisungha`), so nothing secret or private may be committed.[^repo-state]

# Related

- [Development workflow](development-workflow.md)

[^initial-requirements]: Initial requirements stated by Soeren
[^repo-state]: Repository state on 2026-10-04
