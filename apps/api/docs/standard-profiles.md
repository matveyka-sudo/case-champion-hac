# Standard analysis queue

`python -m a2_api.precompute` derives standard requests from the same verified
runtime used by API and worker. It has no city names or pair-specific handlers.
Urban categories are separate jobs. Intercity directions come from registered
profiles; car observations do not create bus schedules. Cargo and pipeline
profiles use their registered physical requirements.

Use the installed backend environment and the same `A2_PRODUCT_RUNTIME_PATH`,
`A2_PRODUCT_RUNTIME_SHA256`, graph/bundle bindings and deployment code version as
the running API. Planning performs runtime validation but no route calculation:

```sh
python -m a2_api.precompute plan \
  --departure-at 2026-11-11T12:00:00+03:00 \
  --deployment-version "$A2_CODE_VERSION" \
  --output /private/waydex/standard-plan.json

python -m a2_api.precompute run \
  --plan /private/waydex/standard-plan.json \
  --base-url http://127.0.0.1:5193/api/v1/ \
  --state /private/waydex/standard-state.json \
  --max-jobs 1 --max-seconds 300
```

An optional repeated `plan --profile ID` selects registered profiles. Inspect
`requests` and `skipped` before scheduling a large plan. No live traffic profiles,
assistant calls or new external-provider configuration are enabled by this tool.
Existing executor quotas and admission limits still apply. A category can exceed
the approved pair budget: preflight rejects it; the planner does not silently
drop origins or increase a limit.

Re-run the identical command to resume. The private checkpoint persists the
session before submission and the stable idempotency key survives a lost HTTP
response. One job runs at a time. Completed jobs are re-read from the API; no
result is manufactured locally. Failed jobs and rejected preflight stop the
batch. `--max-jobs` limits new admissions in each invocation. A time limit stops
new work and polling, leaving at most one admitted job to the existing bounded
worker; it is resumed on the next invocation, not cancelled or duplicated.
The checkpoint is written atomically with permissions 0600 and CLI invocations
using the same state file are locked. Do not commit or share this file: it owns
the session. Use a new state path for a different plan or API server.

## Integration boundary

This is an operator-triggered preparation job, suitable for invoking after a
verified runtime release. It does not run on every page load or API restart.
Snapshot registration already occurs through the immutable runtime manifest;
copying an arbitrary dataset into a directory does not publish it. Automatic
release triggering and a public standard-results catalogue remain separate work.

Currently final-result cache and saved analyses are session-scoped. Preparation
warms that session and the existing executor caches according to their own
identities. It does **not** expose the operator's results to every browser session.
A shared standard-results catalogue must explicitly publish public, immutable
results with source permissions, version identity and fresh session ownership;
removing `access_scope` from the existing cache is not an acceptable shortcut.

The four product states remain independent: registered data, executable profile,
observed result, and eligibility for a complete numerical index. Unknown approach
time or an incomplete catalogue does not become zero when a job succeeds.
