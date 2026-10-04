# Wiki Update Log

## 2026-10-05
* **Update**: [OpenDTU and Hoymiles limit control](research/opendtu-hoymiles-limit-control.md): added the limit response measured on a real HM-1200-4T. Absolute limits act within seconds, relative lowering took minutes, output ramps at about 0.5 % of rated power per second.
* **Update**: [Requirements decisions](project/requirements-decisions.md): Victron access path settled, direct connection to each GX (ADR 0002). Details of the GX devices are in the private installation inventory.
* **Update**: [Overview](project/overview.md): tickets 07 and 18 done; ticket 08 waits for the Victron access path.
* **Update**: Moved the installation inventory out of the published wiki into `llm-wiki/private/`, which git ignores; removed host names from [Overview](project/overview.md).
* **Update**: [Tibber prices](research/tibber-api-prices.md): query confirmed against the real API, error answers recorded. [Overview](project/overview.md): ticket 06 done, trial on production, repository public.

## 2026-10-04
* **Update**: [Requirements decisions](project/requirements-decisions.md): the cross-DTU stuck rule now requires a PV inverter producing at least 50 W; [Overview](project/overview.md): ticket 05 done.
* **Update**: [Overview](project/overview.md): tickets 01 to 04 done.
* **Update**: Installation inventory (private): measured a DTU restart through the API on the real Büro DTU.
* **Update**: [Development workflow](project/development-workflow.md): Sonnet now writes the tests, Haiku only runs them.
* **Update**: Installation inventory (private): Büro 3 confirmed as HM-1200-4T; added API observations made while building ticket 02.
* **Update**: [Overview](project/overview.md) now points to the spec and tickets under `.scratch/nulleinspeisung/`.
* **Update**: Recorded the final interview round in [Requirements decisions](project/requirements-decisions.md); no decisions remain open.
* **Update**: Recorded the third interview round in [Requirements decisions](project/requirements-decisions.md).
* **Creation**: Added draft research pages [PV forecast sources](research/pv-forecast-sources.md) and [Victron ESS charge and discharge control](research/victron-ess-charge-discharge-control.md).
* **Update**: Recorded the second interview round in [Requirements decisions](project/requirements-decisions.md) and Installation inventory (private).
* **Creation**: Added [Requirements decisions](project/requirements-decisions.md) and Installation inventory (private); the glossary `CONTEXT.md` and ADR 0001 were created at the repo root.
* **Creation**: Added draft research page [OpenDTU and Hoymiles limit control](research/opendtu-hoymiles-limit-control.md).
* **Creation**: Added four draft research pages: [app versus integration](research/home-assistant-app-vs-integration.md), [Victron MQTT grid meter](research/victron-mqtt-grid-meter.md), [Tibber prices](research/tibber-api-prices.md), [Shelly Pro 3EM](research/shelly-pro-3em-measurement.md).
* **Ingest**: Saved Soeren's initial requirements to `../raw/2026-10-04-initial-requirements.md` and rewrote [Overview](project/overview.md) from them.
* **Creation**: Added [Overview](project/overview.md) as a draft and [Development workflow](project/development-workflow.md).
* **Initialization**: Created the bundle structure and `../SCHEMA.md` from the LLM-Wiki pattern and OKF v0.2.
