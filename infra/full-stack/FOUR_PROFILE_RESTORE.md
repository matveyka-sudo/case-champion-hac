# Restore the four-profile runtime

This restores the existing 165-file runtime and its separate physical graphs.
Its product manifest is `a68f69593e1aaea8917030e2b2f57433dc41d2ab5569bc3f15bcecfddf813dc6`.
The four profiles are Kaliningrad's dated airport certificate, Moscow walking,
Novorossiysk school walking and Moscow's bounded public-transport timetable.
The Kaliningrad certificate runs offline; this configuration has two routers,
for Moscow and Novorossiysk. It does not enable additional city profiles.

Use Python 3.12. Keep every private input and filled environment file outside
Git. The `ml2-handoff-2026-11-10.zip` restore in the root README remains a separate
step for the official GTFS and provider bindings. That ZIP cannot supply these
graphs, historical proof inputs, databases, observations or provider credentials.
Accepted snapshots also do not reconstruct all original ML2 raw datasets.

## Obtain and restore the small runtime archive

Obtain `threecity-runtime.tar.gz` separately from Git and check these exact pins:

| Item | Value |
| --- | --- |
| Archive bytes | `31748822` |
| Archive SHA256 | `472a108c33f04a91e722df34e777ab089e2dcc000f4de3f258ec36d1d1e14db3` |
| Bundle-manifest SHA256 | `bd779178337c0975d63f3c84dd5e426c354272afb98dac20d9b499bac1c06da2` |
| Declared files / bytes | `165` / `196152474` |
| Certified binding SHA256 | `5b9ce7a83ff5f6301b95568a6d855e786cc2a9c1df1c2e81b6ea30abddb1628e` |

Here `tools` is a checked source checkout containing this runbook and helper;
`python` is its Python 3.12 executable. Use a new destination:

```sh
tools=/absolute/path/to/checked/restoration-source
python=/absolute/path/to/python3.12
private=/absolute/path/to/private/restore-inputs
runtime=/absolute/path/to/new/restored-runtime
"$python" "$tools/infra/full-stack/bundle.py" restore \
  --archive "$private/threecity-runtime.tar.gz" \
  --sha256 472a108c33f04a91e722df34e777ab089e2dcc000f4de3f258ec36d1d1e14db3 \
  --manifest-sha256 bd779178337c0975d63f3c84dd5e426c354272afb98dac20d9b499bac1c06da2 \
  --output "$runtime"
"$python" "$tools/infra/full-stack/bundle.py" verify \
  --directory "$runtime" \
  --manifest-sha256 bd779178337c0975d63f3c84dd5e426c354272afb98dac20d9b499bac1c06da2
```

Expect `165` verified files. Preserve all bytes, including historical source
revision fields, receipts and `.bundle-ready.json`. Do not source the archived
`runtime-settings.json`: its executor/code identity describes an earlier build.

## Transfer the graphs and operator bindings separately

The small archive contains reviewed graph metadata, not the complete PBF/tile
directories. The following five trees are required, totaling **1,359 files and
2,059,159,333 bytes**. The graph helper pins their complete inventories as well
as invoking the existing graph/source/snapshot verifier. It makes no HTTP or SQL
calls. A future graph release needs a separately reviewed inventory; changing
an expected hash to accept different bytes is not restoration.

| Transfer directory | Files | Bytes | Inventory SHA256 |
| --- | ---: | ---: | --- |
| `graphs/moscow` | 76 | 1334725287 | `1a28176f74f8253d834a4c5be57080bb243d71728a1c59b442d719ff3da2c1ec` |
| `graphs/novorossiysk` | 1143 | 644517007 | `cd1a259aa5e0de518615006a0f11eab3c9737f32cfc1f7c777015225372405fa` |
| `graphs/kaliningrad` | 128 | 55236863 | `55ef11572fb49a3bd0c71d23372f6f89319bf3512092c289a24c3ebbe9716b06` |
| `bindings/moscow` | 6 | 22884180 | `03cfe5cd805b080182c7c1b8e25ddc3c9d31021bebf8dcbc38c23d8080bff2c2` |
| `bindings/novorossiysk` | 6 | 1795996 | `e6f55413401d9d6dd690bb7aa54e12dfbf17fc78f69844597809a97b72a4a4cc` |

The inventory SHA256 is over the helper's canonical JSON array of relative path,
byte size, file SHA256 and executable flag. It includes the original Moscow
upstream PBF, its reviewed extract and derivation/tool inputs, all declared
tiles, configuration/profile/generator/version files and the operator bindings.
The existing generic archive limits remain 128 MiB per file / 512 MiB total;
the graph transport retains the existing 1 GiB per source / 5 GiB graph bounds.

On the source machine, set the five paths to the approved existing trees. First
run a read-only dry run; the output destination must not exist:

```sh
graphs=/absolute/path/to/new/graph-transfer
"$python" "$tools/infra/full-stack/four_profile_restore.py" export \
  --runtime "$runtime" \
  --moscow-graph /absolute/path/to/approved/moscow-graph \
  --novorossiysk-graph /absolute/path/to/approved/novorossiysk-graph \
  --kaliningrad-graph /absolute/path/to/approved/kaliningrad-graph \
  --moscow-binding /absolute/path/to/approved/moscow-binding-packet \
  --novorossiysk-binding /absolute/path/to/approved/novorossiysk-binding-packet \
  --output "$graphs" --dry-run
```

Repeat without `--dry-run` to create the transport. Copying is exclusive, streamed
and checked against the already verified inventory. Files become read-only;
`graph-transfer-manifest.json` appears only after every copy succeeds. An
interrupted destination remains available for diagnosis; retry into a new one.
Links, changed sources, extra files and different inventories fail verification.

Valhalla 3.5.1 reports the modification time of its `tiles` directory in
`/status`; a byte-identical copy with a new directory timestamp fails the
existing runtime build-status guard. The v2 transfer receipt therefore records
Moscow and Novorossiysk's source directory timestamps in whole seconds, checks
them against the unchanged artifact status pins, and restores them only after
all descendants have been copied. File hashes and graph bindings are unchanged.
The Kaliningrad profile remains offline and makes no actor-status claim.
Verification rejects old v1 transfers without this metadata; export a fresh
destination instead of altering an old transfer, binding or status expectation.

Transfer the resulting directory through the approved private file channel.
For example, an operator can run `rsync -a -- "$graphs/" operator@server:/private/new-graph-transfer/`
into a fresh destination. Do not use `--delete` on an existing deployment.
Preserve directory modification times on both ends (`rsync -a` includes them).
After transfer, do not touch or recreate the checked router `tiles` directories.
On the recipient, set `graphs` to the received directory and verify again:

```sh
"$python" "$tools/infra/full-stack/four_profile_restore.py" verify \
  --runtime "$runtime" --transfer "$graphs"
```

The graph manifests must verify as:

- Moscow: `a4e30ae109847b6d9858c3e5abf820bb4e81b1b88616c0ee65b41f4a392ffbaa`.
- Novorossiysk: `d2f41ce060cae498126db56fa14c42c9a8396bce2b6ea92403926fd7c03df1f8`.
- Kaliningrad: `1e44413dcb0a19befd40774b40584e3b13d1c879fc09130e6a9633557b4964e3`.

Missing historical bytes require the original source-machine handoff. A current
URL download or newly rebuilt graph cannot silently replace a pinned input.

## Bind the exact source and image identity

Choose an approved frozen integration revision containing the applicable
backend fixes. A graph restoration does not approve a source revision. Create
a clean detached checkout and use its full SHA as `WAYDEX_REVISION`:

```sh
revision=REPLACE_WITH_FULL_APPROVED_COMMIT
release=/absolute/path/to/new/frozen-source
git worktree add --detach "$release" "$revision"
test "$(git -C "$release" rev-parse HEAD)" = "$revision"
test -z "$(git -C "$release" status --porcelain)"
```

Copy `fourprofile.env.example` to a private file, mode `600`, under a directory
mode `700`. Replace every `REPLACE` value and path. Use an unused port and new
Compose project for a recovery rehearsal; preserve production volumes. The
template uses plain `KEY=value` records, with no shell expansion. Both API/worker
and SPA build from `WAYDEX_SOURCE_DIR` at `WAYDEX_REVISION`; do not retag a prior
image as the new commit. Keep passwords out of shell history and saved expanded
Compose output.

```sh
envfile=/absolute/path/to/private/fourprofile.env
four() {
  docker compose --env-file "$envfile" \
    -f "$release/infra/full-stack/compose.product.yaml" \
    -f "$release/infra/full-stack/compose.product-urban-walk.yaml" \
    -f "$release/infra/full-stack/compose.product-novorossiysk-walk.yaml" \
    -f "$tools/infra/full-stack/compose.product-fourprofile.yaml" "$@"
}
four config --quiet
four build api web
docker image inspect --format '{{.Id}}' "waydex-full-api:$revision"
docker image inspect --format '{{.Id}}' "waydex-full-web:$revision"
docker run --rm --network none --read-only --memory 512m --memory-swap 512m \
  --entrypoint /app/.venv/bin/python "waydex-full-api:$revision" -c \
  'from a2_api.adapters.walk_runtime import executor_fingerprint; print(executor_fingerprint())'
```

Set `WAYDEX_WALK_EXECUTOR_SHA256` to that actual image output, then run
`four config --quiet` again. Record image IDs, source SHA and executor hash in
the private recovery receipt. The 165-file runtime's archived fingerprint is
historical metadata; changing the application fingerprint must not alter it.

The overlay explicitly sets 128 MiB cumulative source reads, 32 MiB decoded
worker results, a 600-second job deadline and 2 GiB memory/swap for the checker,
API and worker. The ordinary result wire bound remains 16 MiB. Source documents
retain their existing 32 MiB bound. Demo transport and paid providers are off.
No provider key or assistant environment file is required for these profiles.

Restore optional Alice separately using the [backend integration instructions](../../apps/api/docs/alice-integration.md).
Keep its API credentials private and preserve the existing PostgreSQL budget and
reservation ledger, including charged usage and outstanding holds. Do not use
budget initialization or `set-limit` as a recovery reset. This template restores
the four calculators with Alice off; it does not reproduce the live assistant.
Enabling Alice requires a separately reviewed API-only configuration applied
after this overlay, with its existing budget and explicit provider gates.

Before starting databases or routers, run the real source checker in the image
with networking disabled. This checks the fingerprint and all four profiles,
the native/geometry packet and mounted Kaliningrad graph, without calculating
routes. `$executor` is the value just placed in the private environment:

```sh
executor=REPLACE_WITH_ACTUAL_IMAGE_EXECUTOR_SHA256
docker run --rm --network none --read-only --memory 2g --memory-swap 2g --cpus 2 \
  --tmpfs /tmp:size=128m,mode=1777 \
  --tmpfs /observations:size=64m,mode=1777 \
  --mount "type=bind,source=$runtime,target=/runtime,readonly" \
  --mount "type=bind,source=$graphs/graphs/kaliningrad,target=/certified-graph,readonly" \
  -e A2_RUNTIME_SOURCE_BUDGET_MIB=128 -e A2_WORKER_RESULT_DECODED_MIB=32 \
  -e A2_JOB_TIMEOUT_SECONDS=600 -e A2_DEMO_TRANSPORT_ENABLED=false \
  -e A2_WALK_OBSERVATION_DIR=/observations \
  -e LLM_ENABLED=false -e ALICE_BILLING_CURRENCY_CONFIRMED=false \
  -e A2_PRODUCT_RUNTIME_PATH=/runtime/manifest.json \
  -e A2_PRODUCT_RUNTIME_SHA256=a68f69593e1aaea8917030e2b2f57433dc41d2ab5569bc3f15bcecfddf813dc6 \
  -e A2_CERTIFIED_WALK_MODEL_PATH=/runtime/kaliningrad/proof-binding.json \
  -e A2_CERTIFIED_WALK_MODEL_SHA256=5b9ce7a83ff5f6301b95568a6d855e786cc2a9c1df1c2e81b6ea30abddb1628e \
  -e WAYDEX_RUNTIME_BUNDLE_PATH=/runtime \
  -e WAYDEX_RUNTIME_BUNDLE_SHA256=bd779178337c0975d63f3c84dd5e426c354272afb98dac20d9b499bac1c06da2 \
  -e WAYDEX_GRAPH_PATH=/certified-graph \
  -e "A2_CODE_VERSION=full-$revision-a68f69593e1aaea8917030e2b2f57433dc41d2ab5569bc3f15bcecfddf813dc6-5b9ce7a83ff5f6301b95568a6d855e786cc2a9c1df1c2e81b6ea30abddb1628e-$executor" \
  --entrypoint /app/.venv/bin/python "waydex-full-api:$revision" -c \
  'import importlib.util, os; from a2_api.adapters.walk_runtime import executor_fingerprint; assert executor_fingerprint() == os.environ["A2_CODE_VERSION"].rsplit("-", 1)[1]; s = importlib.util.spec_from_file_location("check", "/app/infra/full-stack/runtime.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); m.product_check(None)'
```

Require all four expected profiles in the output. This offline gate and a clean
Compose render do not establish a successful fresh full-stack recovery. After
the independent database/observation step, startup is `four up -d --no-build
--wait --wait-timeout 180`; retain checker/migration logs, cgroup peak/OOM
events, schema-head output, and actual API/worker/browser acceptance receipts.
Do not infer city-wide minima or a completed Moscow campaign from restoration.

## Preserve database and observations independently

The graph transfer and small runtime archive contain neither PostgreSQL nor
saved observations/sessions. Follow [the backup/restore procedure](README.md#back-up-and-restore)
using `four` in place of `full`. Back up database and observations consistently,
including checkpoint tables, and retain the owning private browser session.
Restore into a fresh project's PostgreSQL and observation volume before
starting migrations/API/worker; observations need owner `10001:10001`.
Without these backups, only a fresh empty deployment can be attempted: previous
jobs, checkpoints, saved analyses and session access have not been recovered.

No existing live project, volume, graph, runtime archive or source directory
needs to be rewritten during these checks.
