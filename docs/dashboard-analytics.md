# Dashboard analytics

The OAN programme dashboards read A2C through `GET /api/v1/charts/<chart_id>`
(`api/v1/dashboard.py`). Every chart is built from **A2C Dashboard Snapshot**, which the
scheduler rebuilds every 15 minutes. No dashboard request aggregates a transactional table.

This is the same standard as the grievance service's dashboard rollups: a scheduled refresh,
a database lock so runs never overlap, and `as_of` on every response.

## Data flow

```
every 15 min (cron */15)      a2c_marketplace/dashboard_rollup.refresh()
  Loan Application, Farmer Profile, Consent Request (+ Data), Audit Event, Participating Bank
      -> A2C Dashboard Snapshot   today's rows replaced; earlier days kept
      -> as_of (system default)

request                        api/v1/dashboard.get_chart()
  Redis cache per chart + filters, keyed on as_of
      -> rows from the latest snapshot only
```

A2C has no immutable event stream to count by day: an application's outcome moves until it is
decided, and the trend is a cohort by the month it was lodged. So every refresh rebuilds the whole
snapshot, which at A2C's volume is a handful of grouped reads. The first build on a site is queued
by `after_migrate`.

## Snapshot families

One table; `family` says which columns a row uses. Only counts, sums and maxima are stored, so any
filter combination is exact.

| Family          | Dimensions                                                | Measures                            |
| --------------- | --------------------------------------------------------- | ----------------------------------- |
| `application`   | bank, place, product, outcome, month lodged               | total, requested and approved value |
| `farmer`        | bank (empty = programme-wide), place                      | total                               |
| `consent`       | bank (empty = programme-wide), place, outcome             | total                               |
| `share`         | bank (empty = programme-wide), place, outcome, first bank | total, fields carried               |
| `share_dataset` | as `share`, plus the requested field's name               | total, last seen                    |
| `decline`       | bank, place, reason                                       | total, requested value              |
| `provider`      | bank                                                      | display fields                      |

- A place is the application's own region and woreda, falling back to its farmer profile's, stored
  as shown and as a key (lower-cased, whitespace collapsed) that filters match.
- Farmers, consents and shares are distinct counts that do not add up across banks, so those
  families carry a programme-wide row (empty bank) and one row per bank. A provider filter reads that
  bank's rows; no provider filter reads the programme-wide ones.
- A failed share is attributed to the bank of the first application that used its consent.
- A decline reason is stored verbatim, but a chart publishes it only when at least five applications
  in the current selection share it; rarer reasons are shown as "Other reasons".

Staleness: at most 15 minutes here, plus whatever the consumer caches (the OAN dashboards cache
another 15 minutes).

## Access

The chart route is guest in Frappe. It is public today; once the Kong gateway enforces
authorization, the gateway checks the dashboards' API key (`DashboardKeyAuth` in the spec, the
`oan-dashboards` consumer in `kong/kong.yml`), and the backend host is not reachable directly. Rows
never carry a farmer name, phone, ID, consent value, document or record id.

## Operations

- Refresh by hand: `bench --site <site> execute oan_a2c.a2c_marketplace.dashboard_rollup.refresh`.
- A new chart is a builder in `api/v1/dashboard.py` plus an entry in `CHARTS`; a new figure is a
  column or family in `a2c_marketplace/dashboard_rollup.py`.
