---
type: Research Finding
title: Home Assistant app versus custom integration
description: What the 2026 "add-on to app" rename means, and whether this project should be built as an app (container) or as a custom integration.
tags: [home-assistant, architecture, app, integration, hacs]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T08:52:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: ha-2026-2
    resource: https://www.home-assistant.io/blog/2026/02/04/release-20262/
    title: Home Assistant 2026.2 release notes (add-ons are now called apps)
    last_modified: 2026-02-04T00:00:00Z
  - id: ha-install
    resource: https://www.home-assistant.io/installation/
    title: Home Assistant installation page (installation types and feature table)
  - id: ha-deprecation
    resource: https://www.home-assistant.io/blog/2025/05/22/deprecating-core-and-supervised-installation-methods-and-32-bit-systems/
    title: Deprecating Core and Supervised installation methods, and 32-bit systems
    last_modified: 2025-05-22T00:00:00Z
  - id: dev-apps
    resource: https://developers.home-assistant.io/docs/apps/
    title: Home Assistant developer docs - Apps (overview)
  - id: dev-apps-config
    resource: https://developers.home-assistant.io/docs/apps/configuration
    title: Home Assistant developer docs - App configuration
  - id: dev-apps-comm
    resource: https://developers.home-assistant.io/docs/apps/communication
    title: Home Assistant developer docs - App communication
  - id: dev-apps-testing
    resource: https://developers.home-assistant.io/docs/apps/testing
    title: Home Assistant developer docs - Local app testing
  - id: dev-config-flow
    resource: https://developers.home-assistant.io/docs/core/integration/config_flow
    title: Home Assistant developer docs - Config flow (incl. reconfigure and subentry flows)
  - id: dev-subentries
    resource: https://developers.home-assistant.io/blog/2025/02/16/config-subentries
    title: Developer blog - Config subentries (found via search result; summary only, page not fetched in full)
    last_modified: 2025-02-16T00:00:00Z
  - id: dev-blog-titles
    resource: https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry
    title: Developer blog posts of 2026-07-21 and 2026-09-15 on device registry config entries (titles only, not read)
    last_modified: 2026-09-15T00:00:00Z
  - id: dev-polling
    resource: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling/
    title: Integration quality scale rule - appropriate polling
  - id: ha-mqtt
    resource: https://www.home-assistant.io/integrations/mqtt/
    title: Home Assistant MQTT integration (discovery)
  - id: hacs-publish
    resource: https://www.hacs.xyz/docs/publish/integration/
    title: HACS - publishing an integration
  - id: phcc
    resource: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
    title: pytest-homeassistant-custom-component
  - id: initial-requirements
    resource: ../../raw/2026-10-04-initial-requirements.md
    title: Initial requirements stated by Soeren
---

# Conclusions

- **The rename is terminology only.** Since Home Assistant 2026.2 (released 2026-02-04) what used to be called an "add-on" is called an "app". Nothing changed technically; an app is still a container image run and managed by the Supervisor.[^ha-2026-2][^dev-apps]
- **Apps need Home Assistant OS.** The installation page lists two installation types, Home Assistant OS (with apps) and Home Assistant Container (without apps).[^ha-install] Supervised and Core were deprecated in 2025.6 and unsupported after 2025.12.[^ha-deprecation]
- **"App" in Soeren's requirement may not mean "Supervisor app".** The requirements say "Home Assistant App" and ask for per-house configuration, adding devices in a UI and selecting inverters per house.[^initial-requirements] Those needs map more directly onto a custom integration (config flow, subentries, devices, entities) than onto a Supervisor app. This is the first thing to settle in the interview.
- **Recommendation (agent's inference): build a custom integration, distributed through HACS as a custom repository.** Reasons are in [Recommendation](#recommendation). The main cost is that the code is Python-only and runs inside the Home Assistant process, so a bug can affect Home Assistant itself.
- **Open:** which installation type Soeren runs was not stated. If it is Home Assistant Container, a Supervisor app is not an option at all.

# What was renamed

| Question | Finding |
| --- | --- |
| What | "Starting with this release, add-ons are now called apps!" Apps are "standalone applications that run alongside Home Assistant"; integrations are "connections that connect Home Assistant to your devices and services".[^ha-2026-2] |
| When | Home Assistant 2026.2, published 2026-02-04.[^ha-2026-2] |
| UI | The settings menu "now contains the Apps items instead of Add-ons".[^ha-2026-2] |
| Old term | "Existing documentation, community posts, and tutorials will continue to reference 'add-ons' for some time"; redirects exist for "add-ons" searches.[^ha-2026-2] |
| Developer docs | The developer documentation section is now `docs/apps/` and uses "app" throughout.[^dev-apps] |
| Not renamed | Integrations, custom integrations and HACS are untouched by the rename (inference from the release notes, which only rename add-ons).[^ha-2026-2] |

# What an app is technically

- "Under the hood, apps are container images published to a container registry."[^dev-apps] The Supervisor installs, starts and updates them.
- An app is described by `config.yaml` plus a `Dockerfile`, `run.sh`, optional `translations/`, `apparmor.txt`, `DOCS.md`, icon and logo.[^dev-apps-config]
- Supported architectures are `aarch64` and `amd64`.[^dev-apps-config] 32-bit architectures were dropped with 2025.12.[^ha-deprecation]
- Any language that runs in a container can be used; the docs name a base image (`ghcr.io/home-assistant/base`) but do not prescribe a language (inference from the Dockerfile-based model).[^dev-apps-config]
- Installation types: only Home Assistant OS offers apps; "Home Assistant Container installations don't have access to apps."[^ha-install]

# Comparison

| Aspect | App (former add-on) | Custom integration (`custom_components/`, via HACS) |
| --- | --- | --- |
| Runs as | Separate container managed by the Supervisor[^dev-apps] | Python package loaded into the Home Assistant Core process (inference from the integration model)[^dev-config-flow] |
| Language | Any (container)[^dev-apps-config] | Python only, asyncio, pinned to the Python and library versions of the installed Home Assistant (inference) |
| Installation types | Home Assistant OS only[^ha-install] | Any installation type, since it is only files under `config/custom_components` (inference); HACS requires one integration per repository under `custom_components/<name>/`[^hacs-publish] |
| Configuration UI | `options` + `schema` in `config.yaml`, rendered as a form on the app's Configuration tab. Types: `str`, `bool`, `int`, `float`, `email`, `url`, `password`, `port`, `match()`, `list()`, `device`. "Nested arrays and dictionaries are supported with a maximum depth of two."[^dev-apps-config] Anything richer needs an own web UI served through ingress (`ingress: true`, default port 8099).[^dev-apps-config] | Config flow for setup, reconfigure flow for changing setup data, options flow for optional settings.[^dev-config-flow] |
| Several houses / devices | Lists of dictionaries in the options form, limited to depth two; no dynamic selectors (for example "pick an inverter discovered on this OpenDTU") without an own web UI (inference from the schema types).[^dev-apps-config] | Config subentries: "the config entry stores authentication details and each location ... is stored as a subentry". Subentry flows are started by the user and support `user` and `reconfigure` steps, not discovery or reauth.[^dev-config-flow] Introduced February 2025.[^dev-subentries] One config entry per house, or one entry with a subentry per house/OpenDTU/inverter, are both possible (inference). |
| Entities and devices | Not native. Either MQTT discovery (`homeassistant/<component>/.../config`, device-based discovery with several components per device, mandatory `origin`)[^ha-mqtt] or the Core REST/WebSocket API. Entities created through MQTT discovery belong to the MQTT integration. | Native: entity platforms (sensor, number, switch, select, button), device registry entries per house, diagnostics, translations (inference from the integration model).[^dev-config-flow] |
| Reading other integrations' entities (Shelly, Tibber, Victron) | Through the Core API proxy: REST at `http://supervisor/core/api/`, WebSocket at `ws://supervisor/core/websocket`, authenticated with `SUPERVISOR_TOKEN`; requires `homeassistant_api: true`.[^dev-apps-comm] State changes arrive over the WebSocket with one extra hop. | Direct, in-process: state machine, state-change listeners and service calls with no network hop (inference). Entity selectors in the config flow let the user pick an existing Shelly or Tibber entity. |
| MQTT access | Declare `services: mqtt:want` (or `need`) and read host, username and password from the Supervisor services API (`bashio::services mqtt "host"`); the app then uses its own MQTT client.[^dev-apps-config][^dev-apps-comm] It can also connect to any other broker, such as the one on each Victron GX device. | Use the MQTT integration's publish/subscribe helpers with `mqtt` as a manifest dependency, or open own client connections (inference). The Victron GX brokers are separate brokers per house, so own client connections are needed either way unless the brokers are bridged. |
| Control loop every few seconds | Unrestricted; it is an own process.[^dev-apps] | Technically possible with a time-interval callback or a coordinator; all I/O must be non-blocking. The quality-scale guidance against aggressive polling ("we should not poll an air quality sensor every 5 seconds") is aimed at core integrations and data that rarely changes; it is not enforced for custom integrations.[^dev-polling] Event-driven control on Shelly state changes avoids polling altogether (inference). |
| Fault isolation | Crash or hang affects only the container; Supervisor watchdog can restart it (`watchdog` key).[^dev-apps-config] | A blocking call or unhandled exception degrades Home Assistant itself (inference). |
| Testing | Devcontainer that "runs Supervisor and Home Assistant, with all of the apps mapped as local apps", or plain `docker build`/`docker run`; logic is tested with whatever the chosen language offers.[^dev-apps-testing] | `pytest-homeassistant-custom-component` extracts Home Assistant's own test fixtures (`hass`, `enable_custom_integrations`, `MockConfigEntry`, snapshot extension), updated daily against Home Assistant releases; the README badge showed 2026.10.0b0 on 2026-10-04.[^phcc] |
| Distribution | App repository (Git repository with `repository.yaml`), images on a container registry.[^dev-apps] | HACS custom repository; mandatory manifest keys `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, `version`; a `brand/icon.png`; GitHub releases recommended.[^hacs-publish] |
| Breakage risk on upgrades | Low; only the REST/WebSocket and MQTT discovery contracts matter (inference). | Higher; internal Home Assistant APIs change. Recent examples from the developer blog index: "Devices are restricted to a single config entry and at most one subentry" (2026-07-21) and a device-entry `config_entries` deprecation (2026-09-15). These posts were seen as search-result titles only and their content was not read.[^dev-blog-titles] |

# Recommendation

Build a **custom integration**. This is the agent's inference from the sources above, to be confirmed in the interview.

1. The requirements are configuration-heavy: several houses, OpenDTUs connected globally, inverters assigned to houses, one meter and one setpoint per house.[^initial-requirements] Config entries plus subentries model exactly this "shared resource with several children" shape,[^dev-config-flow] while the app options form stops at two levels of nesting.[^dev-apps-config]
2. Setpoints, enable/disable switches for the charging strategy and diagnostic sensors become native entities and devices that dashboards and automations can use, without a detour through MQTT discovery.
3. The Shelly and Tibber data may already be present as entities in Home Assistant (see [shelly-pro-3em-measurement.md](shelly-pro-3em-measurement.md) and [tibber-api-prices.md](tibber-api-prices.md)); an integration reads them in-process.
4. It works on every installation type, and the test tooling tracks Home Assistant releases.[^phcc]

Conditions under which an **app** is the better choice:

- The control logic should keep running, or be restartable, independently of Home Assistant Core restarts and upgrades. A Core restart interrupts an integration's control loop; what the Victron and the inverters do in that gap must be defined either way (see [victron-mqtt-grid-meter.md](victron-mqtt-grid-meter.md)).
- A language other than Python is wanted, or the logic should also be usable without Home Assistant.
- A hybrid is possible: a container with the control logic and its own web UI through ingress, exposing entities via MQTT discovery. It costs a second UI and a second configuration store.

# Not answered

- Soeren's installation type (Home Assistant OS or Container) and whether "app" was meant in the Supervisor sense.
- Whether a custom integration may ship in HACS's default list was not researched; a custom repository is enough for private use.[^hacs-publish]
- No measurement was found of the latency added by the Core WebSocket API for an app reading entity states; "one extra hop" above is reasoning, not data.

[^ha-2026-2]: Home Assistant 2026.2 release notes (add-ons are now called apps)
[^ha-install]: Home Assistant installation page (installation types and feature table)
[^ha-deprecation]: Deprecating Core and Supervised installation methods, and 32-bit systems
[^dev-apps]: Home Assistant developer docs - Apps (overview)
[^dev-apps-config]: Home Assistant developer docs - App configuration
[^dev-apps-comm]: Home Assistant developer docs - App communication
[^dev-apps-testing]: Home Assistant developer docs - Local app testing
[^dev-config-flow]: Home Assistant developer docs - Config flow (incl. reconfigure and subentry flows)
[^dev-subentries]: Developer blog - Config subentries (found via search result; summary only, page not fetched in full)
[^dev-blog-titles]: Developer blog posts of 2026-07-21 and 2026-09-15 on device registry config entries (titles only, not read)
[^dev-polling]: Integration quality scale rule - appropriate polling
[^ha-mqtt]: Home Assistant MQTT integration (discovery)
[^hacs-publish]: HACS - publishing an integration
[^phcc]: pytest-homeassistant-custom-component
[^initial-requirements]: Initial requirements stated by Soeren
