---
type: Overview
title: Overview
description: What nulleinspeisungha is, the installation it controls, and where the project stands.
tags: [project]
status: draft
generated: { by: claude-code/claude-sonnet-5-5, at: 2026-11-13T12:00:00Z }
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
2. **Charge from the grid when it pays.** Took over the Node-RED strategy that charged the AC battery from the grid when Tibber rated the price as cheap. Since ticket 32 the integration plans the battery's content over all known prices and buys beforehand only the energy it would otherwise have to buy at prices it cannot choose (shortfall planning); since ticket 37 it holds the battery only when its energy is needed at a dearer time. Details: [Requirements decisions](requirements-decisions.md).

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

- Requirements are settled: see [Requirements decisions](requirements-decisions.md). The spec is `.scratch/nulleinspeisung/spec.md`; tickets live under `.scratch/nulleinspeisung/issues/`, each with its status. As of 2026-11-13 all planned features are built. Released up to 0.11.0 (see `CHANGELOG.md`); the most recent releases brought shortfall planning (0.8.0), the BatteryLife top-up (0.9.0), the Safety Buffer (0.10.0) and steady buffer charging (0.11.0).
- On the branch `review-2026-11` and unreleased: tickets 37 to 41 are done (discharge block on demand, BatteryLife top-up level default 100 %, robustness fixes, the reason sensor, the plan as attributes and diagnostics, the replay tool); ticket 42 (findings of the code review of 2026-11-13: control and load fixes, documentation) is in work.
- Open: 30 (fewer limit commands when the battery is full) waits for observation in spring 2027. Closed: 16 (Node-RED cutover, resolved), 23 (slew rate per inverter, closed as not needed).
- The integration runs on the owner's production Home Assistant and his Node-RED flow is off since 2026-10-06. The repository is public on GitHub (`soeren487/nulleinspeisungha`), so nothing secret or private may be committed.[^repo-state]

# Tools and CI

- `tools/replay.py` replays recorded quarter-hours of one House through the charging planner and compares cost against two baselines (no grid charging; the price-level block of before ticket 37); `tools/export_history.py` writes its input from Home Assistant's history. Real exports go to `llm-wiki/private/`. See `tools/README.md`.
- The GitHub Actions workflow `.github/workflows/ci.yml` runs on pushes to `main`, on pull requests and weekly: tests and linters on the pinned Home Assistant, the tests on the oldest supported Home Assistant, and hassfest and HACS validation.
- The README describes the integration for users, with entity tables in English and German.

# Related

- [Development workflow](development-workflow.md)

[^initial-requirements]: Initial requirements stated by Soeren
[^repo-state]: Repository and installation state, last updated 2026-11-13
