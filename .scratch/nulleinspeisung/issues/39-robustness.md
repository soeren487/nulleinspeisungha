# 39: Robustness findings of the review of 2026-11-13

**What to build:** Small faults found in the review.

**Blocked by:** None

**Status:** resolved

- [x] When Home Assistant stops, the setpoint override and the discharge override the integration set are released on the GX (best effort, the GX also falls back by itself when the grid value stops)
- [x] The sensor "AC battery minimum charge level" no longer shows 0 % for a moment after a start: while the GX has not yet reported a minimum, the value is unknown, and the plan does not count the battery as usable down to 0 % on that momentary value
- [x] `via_device` in device info is replaced by what Home Assistant asks for (deprecated, stops working in 2027.8); it must still work on Home Assistant 2026.9.4
- [x] A DTU request that times out gives a message that says so (today: "Cannot reach DTU: " with nothing after it)
- [x] A limit command that failed is sent again at the next control run (verify with a test; fix if it is not)
- [x] Repeated DTU fetch failures are not logged at ERROR every poll (533 entries in 2.5 days): once when it starts, once when it recovers

## Comments

2026-11-13: Implemented. Retrying a failed limit already worked (test added); the coordinator already logs once per outage, so the 533 log entries were separate outages behind the reverse proxy.
