# Shared Rasp daily quota

Every key-bearing Search or Copyright wire attempt requires a durable quota guard.
The hard ceiling is **500 attempts per account and configured calendar day**,
including explicit retries, HTTP errors, timeouts and uncertain outcomes. The
existing per-instance counter/rate limit remains an additional local bound.
Implementation and testing perform no provider calls or production initialization.

## Central storage and explicit configuration

Use `a2_api.providers.rasp_runtime.open_rasp_provider` as the production lifecycle
factory. It owns an HTTPX client with transport retries disabled, no redirects,
environment proxies or request hooks, and one connection matching concurrency.
The caller closes it through its async context manager. Only an offline
`httpx.MockTransport` may replace this transport in tests.

All releases, workers, sessions and rotated credentials for the same Yandex
account must share the **same central PostgreSQL database/schema and account ID**.
The factory never falls back to an application's `DATABASE_URL` or release volume.
The operator explicitly supplies:

| Setting | Meaning |
| --- | --- |
| `RASP_LIVE_ENABLED` | Defaults to false; explicit true is required. |
| `RASP_API_KEY` | Secret used only in the Authorization header. |
| `RASP_QUOTA_DATABASE_URL` | Separate central ledger connection. |
| `RASP_QUOTA_DATABASE_SCHEMA` | Explicit shared schema at `0006_rasp_quota`. |
| `RASP_ACCOUNT_ID` | Stable account identity shared by all its keys/releases. |
| `RASP_QUOTA_TIMEZONE` | Explicit IANA zone defining this allowance's day. |

Quota timezone is independent of journey dates and origin timezone. No
undocumented upstream reset timezone is assumed. Initialize the central schema
with ordinary backend Alembic migrations, explicitly selecting its URL/schema.
Creating tables grants no allowance and activates no provider.

## Known baseline and daily administration

Already-used usage must be known from an operator-reviewed source. If independent
usage cannot be accounted for, leave the day uninitialized. Initialization attests
that subsequent account usage is exclusively managed by this ledger. Software
cannot discover independent calls made by other applications.

Administration performs no HTTP:

```text
python -m a2_api.providers.rasp_quota_cli status
python -m a2_api.providers.rasp_quota_cli initialize-day \
  --day YYYY-MM-DD --allowance ALLOWANCE_AT_MOST_500 \
  --already-used KNOWN_USED_COUNT --baseline-ref REVIEWED_BASELINE_REFERENCE \
  --exclusive-managed-usage-attested
```

Usage and allowance have no default. Only the current database-clock day in the
configured zone can be initialized. A missing account/day/baseline, wrong key
mapping, unavailable storage or timezone mismatch prevents calls. Tomorrow is
never automatically granted a zero-use allowance.

Identical initialization preserves every reservation. A changed baseline or
allowance blocks the day rather than resetting it. Rotated credentials can be
registered against the same account by identical explicit initialization without
resetting the original baseline or consumption. A credential cannot be rebound to
a fresh account. There is no automatic refund or unblock. Downgrade refuses to
remove initialized quota history.

## Wire authorization and cache

`WireAttemptQuota.reserve_attempt` takes a fresh UUID, credential fingerprint,
canonical request fingerprint and maximum wire timeout. The repository serializes
the account/day row and commits one reservation before returning a one-shot
`AttemptPermit`. Duplicate IDs never authorize another send. A crash, cancellation
or lost commit acknowledgment can waste a unit; it cannot release it.

Permits have a conservative one-second start deadline measured from before the
database round trip. Reservations are refused near day end unless the complete
wire timeout and safety margin fit in the initialized window. The HTTP boundary
checks the permit immediately before its bounded send. Valid cache hits spend no
units and keep their original expiry. Provider caches are local, bounded temporary
observations; the daily counter is shared. Concurrent misses may consume separate
units. Failed responses are not cached as successful observations.

`a2_api.core.rasp_quota.request_fingerprint(host,path,params)` hashes canonical JSON:

```json
{
  "schema_version": "waydex-rasp-wire-request/1",
  "method": "GET",
  "host": "api.rasp.yandex-net.ru",
  "path": "/v3.0/search/",
  "params": {"format": "json"}
}
```

It includes every actual sanitized parameter with sorted keys. Headers and keys
are excluded; credential-like query parameters are rejected. Both supported hosts
and `/v3.0/copyright/` consume the same account allowance. Copyright is requested
explicitly and is never a free startup health check. Logo HTML stays inert data.

## Private capture and ML2 handoff

Optional async `on_page_capture(PageCapture)` receives a bounded successful JSON
response before DTO trimming: exact `raw_json` bytes, endpoint without query,
sorted parameter pairs, aware `fetched_at`, committed `reservation_id` and canonical
`request_fingerprint`. It contains no request headers. A response echoing the key
is rejected before capture. Capture failure is typed, retains the reservation and
does not populate successful cache. There is no default persistence, publication
or automatic real-data retention.

An operator can export selected reservations:

```text
python -m a2_api.providers.rasp_quota_cli export --day YYYY-MM-DD \
  --reservation-id UUID --output NEW_PRIVATE_RECEIPT.json
```

`waydex-rasp-quota-export/1` contains `hard_daily_limit=500`, opaque
`ledger_scope_id`, day/zone, allowance, known usage/reference, attestation/block
state, and reservation IDs, request fingerprints, timestamps and increasing daily
totals. It exports no credentials or credential fingerprints.
`reserved_before_send=true` records committed authorization, not a successful
response. ML2 must bind each new raw page/hash to its own reservation and apply
source/expiry/coverage acceptance independently. Pre-existing captures cannot
receive retrospective authorization. Quota approval does not make observations
accepted timetables or prove transport coverage.

Copyright endpoint documentation:
<https://yandex.ru/dev/rasp/doc/ru/reference/query-copyright>.
