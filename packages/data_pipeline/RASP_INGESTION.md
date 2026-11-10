# Private Rasp observation ingestion

`a2_data_pipeline.rasp_inputs` makes no HTTP requests. It prepares a versioned
temporary service cache from licensed captures. Tests use synthetic responses;
their publication is refused. No real response or production snapshot is
published by this change. Schema:
`data/manifests/schemas/private-rasp-observation-v1.json`.

## Operator retrieval prerequisites

1. Establish the actual account/use authorization, eligible service access,
   current provider terms, explicit cache expiry and purge policy. The official
   documentation review is not authorization for a particular account or
   analytical use. Uses outside the provider terms require applicable permission.
2. Configure BACKEND's durable `WireAttemptQuota` with explicit day timezone,
   known prior usage/baseline, exclusive usage attestation and allowance ≤500.
   Every wire attempt, including retries and fresh copyright requests, reserves
   before sending and is never refunded. Unknown baseline or missing ledger
   stops retrieval. ML2 cannot authorize an earlier unmetered request afterwards.
3. Reuse pinned private station bindings: `node-wd-q21844902` (Moscow Kazanskaya)
   → `node-wd-q3728238` (Novorossiysk). The resolver requires exact ESR bindings,
   known IANA zones and distinct station/city identities. Codes stay private;
   no station-list refetch is needed.
4. Coordinate a hard request budget. First-page discovery costs one attempt;
   fresh copyright adds one. Seven elapsed days intersect seven origin-local
   dates at midnight, eight otherwise. One page/date and three attempts give
   a 21/24-attempt cap per direction. Extra pages, reverse searches and all
   retries consume the same daily allowance; no budget grows automatically.

Use BACKEND's Rasp provider and private `on_page_capture` callback. It supplies
bounded original JSON, endpoint, sanitized parameters, UTC capture time,
reservation ID and fingerprint; no credentials or headers. Store captures
outside Git and public data trees. Query station-to-station with
`transport_types=train`, `transfers=false`, `result_timezone=UTC`, `system=yandex`,
`format=json`, `lang=ru_RU`; page offsets/limits are explicit. ML2 recomputes
BACKEND's `waydex-rasp-wire-request/1` fingerprint. This version supports direct
rail only. Unknown intervals, transfers and unreviewed executions stay excluded.

## Hash-pinned private envelope

Each file reference has relative `path`, `sha256`, `size_bytes` (≤2 MiB). Envelope
and file hashes, duplicate JSON keys, nonfinite numbers and path escapes are
checked; at most 32 pages enter an episode. Required fields:

| Field | Required capture or receipt |
| --- | --- |
| `pages` | Original HTTP200 JSON plus date/offset/limit, retrieval time, reservation ID and request fingerprint. HTTP200 implies no completeness. |
| `quota_authorization` | BACKEND `waydex-rasp-quota-export/1`: hard limit500, opaque ledger scope, quota timezone/day, allowance, explicit prior usage, baseline reference, exclusive attestation, unblocked state and original reservations. |
| `usage_authorization` | `waydex-rasp-cache-usage/1`: `temporary_service_cache_authorized=true`, actual `authorization_ref`, `approved_by`, `purge_policy_ref`, exact `expires_at`, attribution `Данные предоставлены сервисом Яндекс.Расписания`, `attribution_url=https://rasp.yandex.ru/`, `desktop_copyright_banner_required=true`. Operator-reviewed basis, not an automatic legal determination. |
| `physical_identities` | `waydex-rasp-rail-identities/1`, `records`: source `thread_uid`, `service_date` matching source `start_date`, reviewed `physical_trip_id`, `confirmed=true`, actual `review_ref`. Thread UID alone is not physical execution confirmation. |
| `copyright` | Actual copyright JSON preserving provider URL/text and at least one banner. A valid authorized cached response may be reused. Fresh retrieval uses the same quota. Ingestion stores opaque HTML; it never executes it or follows its URLs. |

`retrieved_at` is the beginning of the capture episode. Page timestamps must
satisfy `retrieved_at <= fetched_at <= server_now < expires_at`; future captures
are rejected without a clock-skew allowance. Travel departure/window dates are separate
from wall-clock cache validity. Operator authorization selects expiry; the
documentation supplies no universal TTL. Reservations must precede capture,
match request/day, have unique IDs/counters and satisfy baseline < total ≤
allowance ≤500. A receipt never replaces BACKEND's live durable reservation gate.

## Build and consume

Compute and review the envelope's SHA256. Credentials are never command inputs:

```bash
.venv/bin/python -m a2_data_pipeline.rasp_inputs \
  --envelope /private/service-cache/episode/observation.json \
  --sha256 <reviewed-envelope-sha256> \
  --output /private/service-cache/normalized/episode-id
```

`normalize_rasp(path, expected_sha256, now=aware_datetime)` returns canonical
`AcceptedData`, typed `Timetable`, physical-service/departure bindings, original
copyright fields and QA report. `publish_private` is atomic/exclusive and refuses
synthetic, expired, data-tree or Git-tree output. Existing differing files are
not replaced. New final directories are owner-only `0700`; documents are `0600`
regardless of umask. Immutable reuse and loads reject foreign ownership, links
or unsafe modes without changing existing permissions, including empty
directories. Output: `accepted-data.json`, `timetable.json`,
`physical-services.json`, `copyright.json`, `normalization-report.json`, manifest.

`NormalizedRasp.cache_policy` is a typed `PrivateCachePolicy`, also retained in
the hash-pinned manifest/report. Schema:
`data/manifests/schemas/private-source-validity-v1.json`. It explicitly carries
`observed_at`, `expires_at`, `usage_scope`, `authorization_ref` and `purge_policy_ref`.
BACKEND must preserve it with any frozen snapshot/job and call
`cache_policy.assert_valid(now=server_wall_clock)` at job admission, publication
and cache reuse. Deleting a source file does not revoke an already frozen object.
This handoff is standalone preparation; until those consumer gates are wired,
the normalized timetable is not production-ready.

Every consumer must call `load_private(path, expected_manifest_sha256, now=...)`
on every load and recheck expiry during long jobs. A generic runtime loader
without server-wall-clock expiry checks must not activate these files. Purge
expired episodes and raw/derived copies under the recorded policy. Immutability
means no overwrite during a valid cache episode, not indefinite retention.
This change provides no scheduler or production enablement. FRONTEND/BACKEND
own the approved safe rendering and placement of the original banner/attribution.

## Preserved facts and unknowns

Service dates and aware UTC events are preserved, including >24h clocks. The
backend clock is `local_service_midnight_v1`; lossy ambiguous DST folds are
rejected. Origin is pickup-only departure boundary, destination dropoff-only
arrival boundary. Coincident endpoint fields imply no unobserved dwell or
intermediate stops. The original page remains private until expiry.

Reviewed physical trip/date identity deduplicates copies; conflicting copies
fail. Stable physical departure IDs survive new cache episodes; execution IDs
retain snapshot provenance. Query dates, pagination totals/gaps, source
timestamps, excluded rows and expiry remain in QA. Complete pagination cannot
prove public-network completeness. Schedule/calendar/footpath/transfer/
continuation flags stay false. Reference, transfer time, confirmed absence,
minimum and index remain unknown; territory records provide no boundary polygon
or administrative area.

## Official sources checked 10 November 2026

- [API terms](https://yandex.ru/legal/rasp_api/ru/), §§2.3.2–2.3.5/3.2.4: service use, temporary caching and attribution.
- [Search documentation](https://yandex.ru/dev/rasp/doc/ru/reference/schedule-point-point): dated events, thread identity and pagination.
- [Copyright documentation](https://yandex.ru/dev/rasp/doc/ru/reference/query-copyright): `/v3.0/copyright/`, Authorization header and banner/text/URL.

Capture URLs, timestamps/hashes and local raw-document paths:
`data/manifests/research/rasp-official-docs-2026-11-10.json`. Public documentation
was fetched; provider API calls, credential access and real publications remain zero.
