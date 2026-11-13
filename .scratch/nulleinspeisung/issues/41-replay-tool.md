# 41: Replay recorded days through the planner

**What to build:** A development tool, not part of the integration: `tools/replay.py` reads a file with recorded quarter-hours of one House (start, price, price level, load, PV production, DC Battery support, battery capacity and settings) and replays them: at every quarter-hour it calls the planner with what was known then (prices only as far as they were published: tomorrow's from 13:00), applies the plan to a simulated battery with the real load and PV, and reports per day and in total the energy bought, its cost, the energy from the battery, shortfalls at dear prices, curtailed PV, and the cost against two baselines (no Grid Charging and no Discharge Block; and the rules of the previous release, by a flag where rules are switchable). A second script exports such a file from Home Assistant's history through its REST API (token from the environment, never stored); exported files go to `llm-wiki/private/`, a small synthetic one is checked in as a test fixture.

**Blocked by:** 37, 38

**Status:** resolved

- [x] The replay runs on the synthetic fixture in the test suite and its totals are asserted
- [x] It reports cost with and without Grid Charging
- [x] The export script writes the file format the replay reads; nothing from a real installation is committed

## Comments

2026-11-13: Implemented: `tools/replay.py`, `tools/export_history.py`, two synthetic fixtures. Not yet run on real days (needs an access token for the export). 1026 tests pass on the pinned Home Assistant and on 2026.9.4.
