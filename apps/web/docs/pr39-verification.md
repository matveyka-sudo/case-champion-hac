# PR #39 — integration verification, 11 November 2026

Reviewed frontend head: `b03e974`. Integrated current `dev` `ecdee97` via ordinary
merge `f590d21`; no merge into `dev` was performed. Existing PR training/filter
changes were preserved. Three agents implemented and independently checked the
frontend, provenance and local runtime integration.

## Changes

- Regenerated types and strict runtime schemas from canonical OpenAPI
  `81747303772e…`. Capabilities and cargo scenario DTOs now decode without dropping
  fields or relaxing validation.
- Published presets submit the exact cloned server request through `/analyses`.
  Date, categories, priorities, modes and snapshot are not reconstructed from
  editable defaults. A multi-category preset is not decomposed into a group.
- Cargo `reduce_handling_wait` shows actual handling durations, resource units and
  validity window. Only the saved server proposal is executable.
- Synthetic/nonranking disclosure follows server limitations for every city and
  analysis kind. Historical result disclosure survives selection changes.
  Map camera selection no longer treats every synthetic snapshot as Moscow.
- Mobile results without a point inventory no longer show an empty point count
  or ask the user to choose a nonexistent map point.
- Minimal backend integration fix `9944564`: accepted repeated evidence refs
  crashed demo map validation on a real ML2 Kaliningrad snapshot. Projection now
  preserves unique refs in first-occurrence order. Source data remain unchanged.

## Automated verification

- `npm run api:check`, `npm run typecheck`, `npm run lint`, `npm test`,
  `npm run build`: PASS; final unit run **322 passed**.
- Full Chromium Playwright suite: **93 passed**, 4.6 minutes. After the final
  mobile-only correction, both affected preset/cargo browser tests passed again,
  as did the complete unit/static/build sequence. HTTP-mocked E2E is distinguished
  from the live checks below.
- Separate strict TypeScript compilation of the new test files: PASS.
- Training artifact checks compare 24 examples, 81 category baselines and 27
  scenario values with their ML1 source; no training values were rewritten.
- Backend map/provenance regression: **29 passed**, including two tests reproduced
  failing before the fix. Independent reviewer repeated all eight map tests.
  Backend Ruff/format, strict mypy (138 source files) and schema export check PASS.
- `git diff --check`: PASS. No dependency manifests or lockfiles changed.

## Live local application

Independent PostgreSQL 17, migrations, FastAPI, one worker and same-origin Vite
proxy run on loopback. The canonical local runtime is hash-pinned:
`ca928ada8ac7dc3a0ddf33d1fa63d4df1629f0db3fef8474ce5b9235bc176ba9`.
Its generated artifacts and private database are outside Git.

Fourteen presets were calculated through the ordinary product adapter. Nine are
urban walk/car/PT profiles from unchanged ML2 `.2`; four use accepted intercity
road observations with unknown reference/index; one is a separately labelled
TEST_ONLY cargo fixture, not an ML2 acceptance row. Capabilities and all outputs
carry synthetic/nonranking disclosure. No unknown value is filled with zero.

- Three PT baselines and their saved scenarios completed on the real worker.
  Moscow and Kaliningrad completed the HTTP chain. Moscow also passed the actual
  browser baseline → proposal → scenario flow with no JS/API errors.
- A 180-second HTTP probe for the Novorossiysk scenario expired while waiting
  behind another job. The worker subsequently succeeded in 136.72 computation
  seconds; its immutable PostgreSQL scenario, canonical hash and nonranking on
  both sides were independently validated. This probe is not reported as an
  HTTP PASS. Public transport is queued and can take minutes under load.
- On the final 14-preset runtime, all 11 walk/car/intercity-car/TEST_ONLY cargo
  baselines passed HTTP → worker → saved GET, about 1.1–1.5 seconds each in this
  run. Cargo scenario also passed: handling 7200 → 3600 seconds, delivery
  86400 → 82800 seconds; baseline unchanged and both sides nonranking.
- Actual cargo browser flow passed desktop and 390px mobile, retained comparison
  after refresh without another POST, and produced no JS/API errors or horizontal
  overflow. This uses the running server, without HTTP interception.

The complete 24/21 product matrix is not certified. Public intercity schedules,
ML2 cargo processing/resource scenarios and intra-city pipeline remain outside
this local runtime; missing facts were not invented. Paid external AI/provider
calls are disabled. The TEST_ONLY cargo example must retain its explicit label.
