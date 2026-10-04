# 01: Installable integration skeleton

**What to build:** The integration can be installed and added in Home Assistant under the name "Nulleinspeisung", in English and German, and the automated test suite runs. Adding it creates the single config entry that DTUs and Houses will later hang off. This is the first code in the repository, so it also sets up the project layout, the test harness with a test Home Assistant, and what HACS needs to install it as a custom repository.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The integration can be added once through the Settings UI; a second attempt is refused
- [ ] All texts exist in English and German
- [ ] A test sets up the integration in a test Home Assistant and passes
- [ ] One command runs the tests and one runs the linters; both are documented in CLAUDE.md
- [ ] The repository contains what HACS requires for a custom repository, and the README explains manual installation
