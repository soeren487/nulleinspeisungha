# 25: An idle AC Battery is reported as not answering

**What to build:** A House whose AC Battery is idle (power exactly 0 W, charge level and voltage unchanged) raises the repair issue "AC Battery does not answer" after two minutes, although its GX is connected and fine. The GX publishes a value only when it changes, so an idle battery sends none of the values the integration reads, and the integration takes that silence for a dead GX. The GX publishes a heartbeat every three seconds for as long as the keepalive is sent; the integration must judge freshness by that.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] A GX that sends its values once and afterwards only its heartbeat stays fresh, keeps its last values, and raises no issue
- [x] A GX that sends neither values nor heartbeat for 60 seconds is not fresh, and the issue is raised after two minutes as before
- [x] Battery headroom and consumption keep working while the battery is idle

## Comments

2026-10-06: Reported by the owner for both Houses after installing 0.3.0. Cause established on the real devices: a GX publishes a value only when it changes, and one of the batteries sat idle at exactly 0 W, sending nothing for 90 s, while its heartbeat arrived every 3 s. Fixed on branch `ticket-25-idle-battery`: freshness now counts any message from the GX, including the heartbeat. 620 tests pass, linters clean. The fixed gateway was also run for 170 s against a real GX and stayed fresh, though the battery was not idle at that moment; the idle case is covered by the simulation.
