# 13: Fetch Tibber prices per House

**What to build:** The owner enters one Tibber token and chooses a Tibber home per House. The House then shows the current price and Price Level, and holds quarter-hour prices with Price Level for today and, once published, tomorrow.

**Blocked by:** 04 (Create a House with Grid Meter and Inverters)

**Status:** resolved

- [x] The token is entered once; an invalid token is reported in the form
- [x] Each House selects one of the account's homes
- [x] The House shows current price and Price Level, updating at each quarter-hour
- [x] Tomorrow's prices are picked up when they are published
- [x] Unavailable prices raise a repair issue and clear when prices return
- [x] Tests run against a simulated Tibber API at the HTTP boundary

## Comments

2026-10-05: Implemented on branch `ticket-13-tibber-prices`. 276 tests pass, linters clean. The client was run read-only against the real Tibber API: both homes found, 192 quarter-hour prices each, current price and level resolved. The token is entered in the integration's options; each House selects its Tibber home in its first form step. Prices are fetched at setup, then only when they run out or, from 13:00 local time, every 15 minutes plus a random delay until tomorrow's prices are present. Repair issues and notifications now live in a shared alerts module used by DTUs and prices.
