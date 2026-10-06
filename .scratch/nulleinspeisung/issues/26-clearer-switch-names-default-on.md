# 26: Clearer names for the House switches, on by default

**What to build:** The two House switches get names that say what they do, and both are on for a newly created House. German: "Einspeisung regeln" (was "Abregelung") and "Netzleistung an Victron senden" (was "Netzleistung melden"). English: "Regulate feed-in" and "Send grid power to Victron". A House that already exists keeps the state its switches have.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Both switches show the new names in German and English; their entity ids and unique ids do not change
- [x] For a new House both switches are on from the start; an existing House keeps its restored switch states
- [x] The second switch still exists only for a House with an AC Battery and a grid meter topic

## Comments

2026-10-06: The owner's decision after reviewing all control names. Implemented on branch `ticket-26-switch-names-default-on`; 629 tests pass, linters clean. A stored off is protected by the start-up order: the switches only set flags while the platforms load, and the control loop, the publisher and the override release start afterwards. New Houses keep the previous entity ids. Open with the owner: the section heading "Konfiguration" on the device page comes from Home Assistant and cannot be renamed by an integration.
