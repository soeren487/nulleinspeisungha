---
type: Research Finding
title: Tibber price data - GraphQL API and the Home Assistant integration
description: How to get Tibber quarter-hour prices and price levels per home from the GraphQL API, and what the official Home Assistant Tibber integration exposes of them.
tags: [tibber, api, graphql, prices, home-assistant]
status: draft
generated: { by: claude-code/claude-opus-5-5, at: 2026-10-04T08:52:08Z }
stale_after: 2027-04-04T00:00:00Z
sources:
  - id: tibber-guide
    resource: https://developer.tibber.com/docs/guides/calling-api
    title: Tibber Developer - Communicating with the API (text extracted from the site's JavaScript bundle on 2026-10-04)
  - id: tibber-reference
    resource: https://developer.tibber.com/api/reference.md
    title: Tibber GraphQL schema reference (auto-generated from api.tibber.com)
  - id: tibber-changelog
    resource: https://developer.tibber.com/api/changelog.md
    title: Tibber API changelog (latest entry 2025-09-01)
    last_modified: 2025-09-01T00:00:00Z
  - id: tibber-data-api-auth
    resource: https://data-api.tibber.com/docs/auth/
    title: Tibber Data API - Authentication
  - id: ha-tibber-docs
    resource: https://www.home-assistant.io/integrations/tibber/
    title: Home Assistant - Tibber integration documentation
  - id: ha-tibber-source
    resource: https://github.com/home-assistant/core/tree/dev/homeassistant/components/tibber
    title: Home Assistant core, tibber component source (dev branch, read 2026-10-04; requires pyTibber 0.37.6)
  - id: pytibber
    resource: https://github.com/Danielhiversen/pyTibber
    title: pyTibber source (master, read 2026-10-04; latest release 0.38.0 of 2026-08-10)
    last_modified: 2026-08-10T03:24:52Z
  - id: initial-requirements
    resource: ../../raw/2026-10-04-initial-requirements.md
    title: Initial requirements stated by Soeren
---

# Conclusions

- **One query gives everything needed.** `priceInfo(resolution: QUARTER_HOURLY)` on a home's `currentSubscription` returns `today` and `tomorrow` with 96 items each, each item carrying `total`, `energy`, `tax`, `startsAt`, `currency` and `level`.[^tibber-changelog][^tibber-reference]
- **Quarter-hour prices exist since 2025-10-01** and must be requested explicitly; the default resolution is still `HOURLY`.[^tibber-changelog]
- **One personal token covers all homes of one Tibber account** (`viewer.homes`).[^tibber-reference] Two houses under two different Tibber accounts need two tokens (inference). Ask Soeren which applies.
- **The Home Assistant Tibber integration is not a full substitute.** It exposes the current price as a sensor and quarter-hourly `start_time` + `price` for today and tomorrow through the `tibber.get_prices` action, but **no `level`, no `energy`/`tax` split**, and the action reads only the first Tibber config entry.[^ha-tibber-source] If the charging strategy keeps using Tibber's CHEAP / VERY_CHEAP rating, the app must call the API itself or compute its own rating from prices.
- **No API policy change in 2026 was found.** The changelog's newest entry is 2025-09-01.[^tibber-changelog] Tibber's newer Data API calls the GraphQL API "older" and says personal access tokens exist there "for historical reasons", but no deprecation date for the GraphQL API was found.[^tibber-data-api-auth]
- **No official numeric rate limit was found** in the current guide. See [Rate limits](#rate-limits).

# API basics

| Item | Value |
| --- | --- |
| Endpoint | `https://api.tibber.com/v1-beta/gql`, HTTP POST with JSON body `{"query": "..."}`[^tibber-guide] |
| Auth header | `Authorization: Bearer <token>`[^tibber-guide] |
| Token types | Personal Access Token ("gives you access to your data and your data only ... ideal for DIY people") from developer.tibber.com, or an OAuth client for apps distributed to other users.[^tibber-guide] |
| Mandatory header | "Clients must set the `User-Agent` HTTP header when calling the GraphQL API. Both platform and driver version must be indicated."[^tibber-guide] |
| Retry rule | "Clients must implement jitter and exponential backoff when retrying queries."[^tibber-guide] |
| Auth errors | Since 2025-01-03 an invalid or missing token returns HTTP 200 with `errors[].extensions.code = UNAUTHENTICATED`, no longer HTTP 400.[^tibber-changelog] |
| Live data | WebSocket subscriptions (`liveMeasurement`), URL from `viewer.websocketSubscriptionUrl`, at most two open sockets. Needs a Tibber Pulse or similar; not needed for prices.[^tibber-guide] |

# Price query

```graphql
{
  viewer {
    homes {
      id
      appNickname
      timeZone
      currentSubscription {
        priceInfo(resolution: QUARTER_HOURLY) {
          current  { total energy tax startsAt currency level }
          today    { total energy tax startsAt currency level }
          tomorrow { total energy tax startsAt currency level }
        }
      }
    }
  }
}
```

- `viewer.homes` is "All homes visible to the logged-in user"; a single home is addressed with `viewer.home(id: "...")`.[^tibber-reference] The query above is assembled from the schema and the changelog example. It was run against the real API on 2026-10-05 with Soeren's token: HTTP 200, 96 items each for today and tomorrow, levels as documented. An invalid token also answers HTTP 200, with `errors[0].extensions.code` = `UNAUTHENTICATED` and `data: null`; an unknown home id gives code `HOME_NOT_FOUND`. Sanitised answers are in `tests/fixtures/tibber/`.
- Tibber recommends smaller queries, one concern per request, over one large query for all homes.[^tibber-guide] pyTibber queries prices per home id.[^pytibber]
- A home without an active subscription returns `currentSubscription: null`; pyTibber handles that case explicitly.[^pytibber]

## Fields

| Field | Meaning |
| --- | --- |
| `total` | "The total price (energy + taxes)"[^tibber-reference] |
| `energy` | "Nord Pool spot price"[^tibber-reference] |
| `tax` | "The tax part of the price (guarantee of origin certificate, energy tax (Sweden only) and VAT)"[^tibber-reference] |
| `startsAt` | "The start time of the price"; ISO 8601 with UTC offset in the documented example (`2017-10-11T19:00:00+02:00`).[^tibber-reference][^tibber-guide] |
| `currency` | "The price currency"[^tibber-reference] |
| `level` | "The price level compared to recent price values"[^tibber-reference] |

Whether `total` for German homes contains grid fees and levies beyond what the `tax` description lists is not stated in the reference; no reliable answer found.

## Price level

"Price level based on trailing price average (3 days for hourly values and 30 days for daily values)".[^tibber-reference]

| Level | Price relative to the trailing average |
| --- | --- |
| `VERY_CHEAP` | 60 % or less |
| `CHEAP` | above 60 % up to and including 90 % |
| `NORMAL` | above 90 % and below 115 % |
| `EXPENSIVE` | 115 % up to below 140 % |
| `VERY_EXPENSIVE` | 140 % or more |

Thresholds as given in the schema reference.[^tibber-reference] Not stated there: whether the average is taken over `total` or `energy`, and whether quarter-hour items use the same 3-day window (the text only names "hourly values"). No reliable answer found; for a reproducible strategy the app may be better off computing its own rating from `total` (agent's suggestion).

## Quarter-hour resolution

- "The `Subscription.priceInfo` field ... now takes an optional argument `resolution` that can either be `HOURLY` or `QUARTER_HOURLY`. If not specified, `resolution` will default to `HOURLY`".[^tibber-changelog]
- "the API will return 96 price items each when `resolution` is set to `QUARTER_HOURLY`. Likewise, the field `current` will be returning the price for the current quarter-hour."[^tibber-changelog]
- Day-ahead trading moved to quarter-hour units on 2025-09-30; "The first day with quarter-hourly prices will be the subsequent day, October 1, 2025."[^tibber-changelog]
- Tibber may later change the default to `QUARTER_HOURLY`, "communicated here well in advance"; passing the argument explicitly is safe either way.[^tibber-changelog]
- Days with a daylight-saving change have 92 or 100 quarter-hours (general fact, not from the Tibber docs).
- History: `Subscription.priceInfoRange`, capped at 672 quarter-hour items per request; `PriceInfo.range` and `Subscription.priceRating` are deprecated.[^tibber-reference][^tibber-changelog]

## When tomorrow's prices appear

- Tibber's docs give no clock time. They say: "You still only need to successfully fetch tomorrow's prices once a day, as prices are only published once a day", and recommend "a long random delay when querying prices; if you can wait an hour or two after tomorrow's prices normally come in, you have a higher chance of retrieving them on first try."[^tibber-changelog][^tibber-guide]
- Evidence from clients: the Home Assistant `get_prices` action treats its cache as stale for tomorrow from 13:00 local time, and the price coordinator starts polling for tomorrow at a random point between 14:00 and 22:00 local time.[^ha-tibber-source] This matches the day-ahead auction publishing around 13:00 CET (common knowledge, not sourced here).
- Consequence for the strategy "full by the end of the coming night": before roughly 13:00-14:00 the prices after midnight are unknown, so an evening plan can cover the whole night, a morning plan cannot (inference).[^initial-requirements]

# Rate limits

- The current guide states no number. It gives behaviour rules instead: "Query prices only once per day for today and tomorrow and cache the result"; "Do try to avoid using `PriceInfo.current`; it is better to fetch prices once per day, store them locally ... and then pick the current price from there".[^tibber-guide][^tibber-changelog]
- A figure of 100 requests per 5 minutes per IP address circulates from older versions of the Tibber docs. It is the agent's recollection and could not be confirmed in the current documentation; do not rely on it.
- A price fetch a few times per day per home is far below any plausible limit (inference).

# Token and policy status through 2026

| Point | Finding |
| --- | --- |
| Personal access token | Still described as the way for DIY use in the current guide.[^tibber-guide] |
| Data API | Separate REST API at `data-api.tibber.com` for connected devices, OAuth2 authorization-code flow only: "the Data API does not [support personal access tokens], and we have no plans to support them"; it refers to "the older GraphQL API".[^tibber-data-api-auth] Whether the Data API offers prices was not established. |
| GraphQL deprecation | None announced in the changelog (newest entry 2025-09-01).[^tibber-changelog] The "older" wording is a signal to watch, not a date. |
| Home Assistant integration | Its config flow is now an OAuth2 flow requesting Data API scopes (`data-api-homes-read` and others); the documentation still also mentions a token from developer.tibber.com.[^ha-tibber-source][^ha-tibber-docs] Which of the two a fresh setup offers in the current release was not verified on a running instance. |

# What the Home Assistant Tibber integration exposes

| Data | Available | Detail |
| --- | --- | --- |
| Current price | Yes | Price sensor per home; state is `total` of the current quarter-hour (pyTibber matches the 15-minute slot).[^ha-tibber-source][^pytibber] |
| Sensor attributes | Partly | `max_price`, `avg_price`, `min_price`, `off_peak_1`, `peak`, `off_peak_2`, `intraday_price_ranking`. No list of future prices, no `level`.[^ha-tibber-source] |
| Future prices | Yes, via action | `tibber.get_prices` with optional `start` and `end`; response `{"prices": {"<home nickname>": [{"start_time": ..., "price": ...}, ...]}}`. Data comes from pyTibber's `price_total`, which is filled from `priceInfo(resolution: QUARTER_HOURLY)` for today and tomorrow, so the entries are quarter-hourly even though the documentation still says "hourly".[^ha-tibber-source][^pytibber][^ha-tibber-docs] |
| `level` | No | pyTibber requests `level` but stores only `startsAt` → `total`; neither the sensor nor the action returns it.[^pytibber][^ha-tibber-source] |
| `energy`, `tax` per slot | No | Only `total` is kept.[^pytibber] |
| Several homes | Within one account | The action loops over all active homes of the connection and keys the result by home nickname. It uses only `entries[0]`, the first Tibber config entry, so homes on a second Tibber account are not returned.[^ha-tibber-source] |
| Refresh | Automatic | Price coordinator re-evaluates at quarter-hour boundaries; fetch coordinator polls at random 1-10 minute intervals but only calls the API when today's prices are missing or tomorrow's are due.[^ha-tibber-source] |

Reuse options (agent's assessment):

1. **Reuse the integration** through `tibber.get_prices`: no token handling, no extra API load, quarter-hour totals for today and tomorrow. The app must derive "cheap" itself, and depends on the integration being installed and on the response shape of an action that is keyed by a user-editable nickname.
2. **Call the API directly** with a personal token and own User-Agent: gets `level`, `energy`, `tax` and a stable home id; costs token configuration per account and adds one more client to an API Tibber describes as "highly congested".[^tibber-guide]
3. Both are compatible with a custom integration or an app; see [home-assistant-app-vs-integration.md](home-assistant-app-vs-integration.md).

# Open questions for the interview

1. Are both houses homes of one Tibber account, or separate accounts?
2. Does the Node-RED flow use Tibber's `level` (CHEAP / VERY_CHEAP) or its own threshold on prices? "Rated as cheap or super cheap" suggests `level`.[^initial-requirements]
3. Is the Home Assistant Tibber integration already installed, and is depending on it acceptable?

[^tibber-guide]: Tibber Developer - Communicating with the API (text extracted from the site's JavaScript bundle on 2026-10-04)
[^tibber-reference]: Tibber GraphQL schema reference (auto-generated from api.tibber.com)
[^tibber-changelog]: Tibber API changelog (latest entry 2025-09-01)
[^tibber-data-api-auth]: Tibber Data API - Authentication
[^ha-tibber-docs]: Home Assistant - Tibber integration documentation
[^ha-tibber-source]: Home Assistant core, tibber component source (dev branch, read 2026-10-04; requires pyTibber 0.37.6)
[^pytibber]: pyTibber source (master, read 2026-10-04; latest release 0.38.0 of 2026-08-10)
[^initial-requirements]: Initial requirements stated by Soeren
