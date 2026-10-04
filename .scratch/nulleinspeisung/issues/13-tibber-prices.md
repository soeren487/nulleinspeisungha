# 13: Fetch Tibber prices per House

**What to build:** The owner enters one Tibber token and chooses a Tibber home per House. The House then shows the current price and Price Level, and holds quarter-hour prices with Price Level for today and, once published, tomorrow.

**Blocked by:** 04 (Create a House with Grid Meter and Inverters)

**Status:** ready-for-agent

- [ ] The token is entered once; an invalid token is reported in the form
- [ ] Each House selects one of the account's homes
- [ ] The House shows current price and Price Level, updating at each quarter-hour
- [ ] Tomorrow's prices are picked up when they are published
- [ ] Unavailable prices raise a repair issue and clear when prices return
- [ ] Tests run against a simulated Tibber API at the HTTP boundary
