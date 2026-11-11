# Real Kaliningrad walking pilot

These commands reproduce the real, bounded `kaliningrad-urban-2026-11-10.2`
point → school calculation. Use an integrated checkout containing the walk API
adapter and the engine's explicit six-decimal correlation support. Python 3.12,
uv 0.12.17, Docker and the installed workspace packages are required:

```sh
uv sync --frozen --all-packages --group dev
export A2_DATA_DIR="$PWD/data"
export WAYDEX_RUNTIME="$(mktemp -d "${TMPDIR:-/tmp}/waydex-real-e2e.XXXXXX")"
```

Phase 2 also accepts the explicitly selected source revision
`--bundle-id kaliningrad-urban-2026-11-10.3`. Pass this option consistently to
`select`, `artifact` and `verify`, and choose a new artifact filename when reusing
a verified graph. The default remains `.2`; an artifact/snapshot mismatch is an
error, never a fallback. Revision `.3` has 77 accepted school sites and retains
partial catalog coverage. It adds one independently verified municipal campus;
it does not make any route minimum or city index proven. The API/worker must use
the same newly generated artifact hash, not the old snapshot's artifact.

Run from the repository root. Keep the runtime outside the checkout. No PBF,
tiles, generated artifacts, GTFS or Yandex bindings are committed here. These
scripts make zero Yandex requests and need no paid API. The supplied bundle stays
unchanged; its loader checks file hashes and canonical AcceptedData validation.

## Build and observe

The image is pinned to the inspected multi-platform **3.5.1** digest. The PBF,
serializer source and configuration generator have fixed SHA256 values in
`runtime.py`. A different image version is not a fallback.

```sh
docker pull ghcr.io/valhalla/valhalla@sha256:4ac3d13d1019f9a130e9e6b6924c16ea55da0fa51e3653039152e33ad4095661
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" fetch
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" build
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" serve
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" \
  --point-id kaliningrad-utm34n-1km-v1-e468-n6061-p \
  --destination-id kaliningrad-school-klgd_education-4409878 select
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" artifact
.venv/bin/python infra/real-e2e/runtime.py --runtime "$WAYDEX_RUNTIME" verify
```

Defaults reserve container `waydex-e2e-valhalla` and loopback port **8007**;
choose a unique `--name`, `--port` and matching `--endpoint` for another run.
`serve` mounts the graph read-only. `build` refuses existing graph/config/log
outputs; failed builds stay available for diagnosis and require a fresh runtime.
Downloads and generated files are created exclusively, never overwritten.

The build manifest records the image identity, actual binary version, source,
config, profile, tile hashes, command and elapsed time. The accepted PBF is
`kaliningrad-261107.osm.pbf`, SHA256
`769a6a6ac552b534057f2480b88f1c2d0709636761ab6d08483efe69c1e97b69`.
Walking speed **5 km/h** is an explicit model assumption. Administrative/timezone
DBs are not collected; the graph uses source PBF administrative tags. This pilot
uses static pedestrian routing.

`select` with the explicit IDs above validates the exact accepted pilot pair and
its actual `/locate` projections. It rejects unknown, quarantined or unconfirmed
endpoints. Omit both IDs only to scan accepted traversable points and schools,
record rejected candidates, and order compatible pairs by angular distance solely
to pick a new smoke-test candidate. It never claims nearest travel
time. Six-decimal locate rendering must contain the original access coordinate
within half a final decimal unit; route input echoes must also be compatible.
Larger shifts are rejected. The route request retains original access coordinates.

`artifact` uses the first candidate, derives both connector durations from the
accepted model profile, observes actual locate/route responses, checks foot-only
maneuvers and ferry absence, and normalizes the result before publishing JSON.
The full AcceptedData school roster remains present. Other unexecuted pairs stay
blocked, catalog coverage stays partial, and no minimum-time proof is supplied.
Raw HTTP requests/responses are saved by response hash; the artifact contains
resolvable evidence references, build/profile identity and expected `/status`.

## Launch the API and one worker

Use an isolated PostgreSQL database/schema and configure `DATABASE_URL` and
`DATABASE_SCHEMA` through the checkout's private `.env` or process environment.
Both processes must use the same values. Apply migrations to that isolated schema:

```sh
.venv/bin/alembic -c apps/api/alembic.ini upgrade head
export WAYDEX_ARTIFACT="$WAYDEX_RUNTIME/walk-pilot-artifact.json"
export WAYDEX_ARTIFACT_SHA256="$(.venv/bin/python -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$WAYDEX_ARTIFACT")"
export WAYDEX_IMPLEMENTATION="$(git rev-parse HEAD)"
```

In separate terminals, with those same exports and checkout:

```sh
.venv/bin/python infra/real-e2e/launch.py api \
  --artifact "$WAYDEX_ARTIFACT" --sha256 "$WAYDEX_ARTIFACT_SHA256" \
  --evidence-dir "$WAYDEX_RUNTIME/observations" \
  --code-version "$WAYDEX_IMPLEMENTATION" --port 8008
```

```sh
.venv/bin/python infra/real-e2e/launch.py worker \
  --artifact "$WAYDEX_ARTIFACT" --sha256 "$WAYDEX_ARTIFACT_SHA256" \
  --evidence-dir "$WAYDEX_RUNTIME/observations" \
  --code-version "$WAYDEX_IMPLEMENTATION"
```

The launcher checks the artifact hash, uses installed packages from the checkout's
`.venv`, selects the real registry/preflight/calculation hooks, sets `A2_DATA_DIR`,
and includes the full artifact hash in `A2_CODE_VERSION`. It preserves the operator's
database settings and does not migrate or initialize a database. For an existing
process supervisor, `runtime.py ... env` prints the four artifact-related exports;
the matching hooks are in `launch.py`.

The resulting analysis supports Kaliningrad / walk / static / school only.
Expect a numeric observed journey, partial completeness, unknown category/city
indices and disabled ranking. Network geometry covers the network segment only;
physical connectors enter total time once at each endpoint. A scenario requires
its own supported backend operation and is not implied by a successful walk job.

## Read-only verification of an existing runtime

Use the recorded artifact filename and exact checksum. This command hashes the
PBF/config/profile/tiles, checks the artifact's graph-manifest identity and live
`/status`, then observes and normalizes the real pair again. It writes no runtime
files and does not rebuild or restart the graph:

```sh
.venv/bin/python infra/real-e2e/runtime.py \
  --runtime "$WAYDEX_RUNTIME" --artifact walk-pilot-artifact.json \
  --expected-artifact-sha256 "$WAYDEX_ARTIFACT_SHA256" verify
```

The initial live acceptance used point
`kaliningrad-utm34n-1km-v1-e468-n6061-p` and school
`kaliningrad-school-klgd_education-4409878` (МАОУ СОШ № 12):
519.204 seconds network + 9.7 and 18.5 seconds connectors = 547.404 seconds.
It retained 75 blocked school pairs, `minimum_proven=false`, partial completeness,
null territory index and `ranking_eligible=false`. Those are observed checks,
not a required result for a new graph build or evidence of city-wide coverage.
