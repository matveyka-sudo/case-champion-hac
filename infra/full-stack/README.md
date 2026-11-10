# Isolated full stack

This deployment builds the API-mode React SPA and runs Nginx, FastAPI, one
database-backed worker, migrations, PostGIS and the pinned real Valhalla graph.
It uses its own Compose project and volumes. The older `infra/waydex` deployment,
its `waydex_postgres_data` volume and other running containers are unaffected.

The initial runtime supports the reviewed Kaliningrad walk/static/school pilot.
It returns real observed journeys with partial completeness, unknown scores and
disabled ranking. Running this stack does not establish city-wide route minima,
public transport, freight or paid-provider coverage. The source PBF, graph,
historical observations and hash-pinned parent artifact are external inputs;
they are deliberately absent from Git and image build contexts.

Docker Engine, Compose v2 with completed-service dependencies, Buildx and Python
3.9+ are required. The API uses Python 3.12 and uv 0.12.17 inside the image; local
Python packages and `.venv` are unnecessary. PostGIS currently uses its reviewed
amd64 image, including on arm64 Docker hosts. All six base/runtime images use
content digests. Allow about 5 GiB of container memory plus image build space.

## Prepare a clean release

Export the selected committed revision from a checkout; do not build a home
directory or include local dependencies. Keep release secrets outside the source.

```sh
revision=$(git rev-parse HEAD)
release="/absolute/path/to/releases/$revision"
mkdir "$release"
git archive "$revision" | tar -x -C "$release"
```

Copy `.env.example` to a private file outside that release and set mode `600`.
Set a unique `COMPOSE_PROJECT_NAME` beginning with `waydex-full-`, an unused
loopback HTTP port, exact revision and absolute paths. Choose a new password
containing hexadecimal characters (`openssl rand -hex 32`). Do not reuse a
previous deployment's project name or PostgreSQL volume. `WAYDEX_GRAPH_DIR`
must contain the outputs described in [the graph runbook](../real-e2e/README.md),
including the source PBF, serializer, configuration generator, version, config,
profile, build manifest and every declared tile. Use `runtime.py fetch/build`
there to reproduce those external inputs; using an existing graph checks it
without rebuilding or altering it.

Set `WAYDEX_PARENT_ARTIFACT` to the original, already accepted walk artifact and
`WAYDEX_PARENT_SHA256` to its independently recorded exact hash. Preserve that
original. Create a fresh deployment directory, then bind the endpoint once:

```sh
python3 "$release/infra/full-stack/runtime.py" prepare \
  --graph /absolute/path/to/verified/graph \
  --parent /absolute/path/to/original/walk-pilot-artifact-accepted-3.json \
  --parent-sha256 RECORDED_PARENT_SHA256 \
  --output /absolute/path/to/new/deployment/walk-artifact.json
```

Record the printed digest as `WAYDEX_ARTIFACT_SHA256`. `prepare` checks the
parent checksum and graph identity, every source/config/profile/tile checksum,
the complete tile inventory, binary version and pinned image. It exclusively
creates a read-only derivative outside the graph. Its only behavioral change is
`endpoint_url=http://valhalla:8002`, plus explicit parent and graph binding
metadata. Historical evidence URLs, dates and observations remain intact. This
is a deployment binding and supplies no new routing proof. Existing output,
wrong hash, wrong graph and missing external data are errors.

## Build and start

Use the same private environment file and source revision for every command:

```sh
release_env=/absolute/path/to/private/full-stack.env
full() {
  docker compose --env-file "$release_env" \
    -f "$release/infra/full-stack/compose.yaml" "$@"
}
full config --quiet
full build api web
full pull postgres valhalla
full up -d --no-build --wait --wait-timeout 180
full ps -a
```

The graph is verified before Valhalla starts. A second check observes its live
status, locate and foot route, normalizes the route against the accepted source
bundle and exercises the API's artifact loader. Only then can migrations run
against the new healthy database. API and worker start after migrations; Nginx
waits for both. A failed check stops dependent services. API and worker share
identical code, accepted bundles, runtime hashes, schema and writable observation
volume. The worker readiness check verifies its process and database tables;
the smoke calculation below verifies actual queue processing. Backend advisory
locks enforce a single active calculation worker per schema.

Only `127.0.0.1:WAYDEX_HTTP_PORT` is published. Nginx preserves same-origin
Host, Origin and session cookies when proxying `/api/v1/` and `/health`. The SPA
is built with `VITE_DATA_MODE=api`. A public HTTPS deployment needs a separately
configured TLS reverse proxy and secure cookies; this loopback profile contains
no certificate management. Runtime roots and graph mounts are read-only, API
and worker use UID 10001, and each container has CPU, memory, PID and rotating
log limits. PostgreSQL and observations use volumes scoped to the unique project.

## Verify a real job and persistence

The smoke script submits a real job with a private cookie session, waits for the
worker, and asserts partial results and unknown scores. Keep its output outside
the release. Cookies are private; do not publish them with acceptance evidence.

```sh
python3 "$release/infra/full-stack/smoke.py" \
  --base-url http://127.0.0.1:5190 --evidence-dir /absolute/path/to/private/smoke
full exec -T postgres psql -X -v ON_ERROR_STOP=1 -U waydex -d waydex \
  -c 'SELECT PostGIS_Full_Version();'
full run --rm --no-deps migrate /app/.venv/bin/alembic \
  -c /app/apps/api/alembic.ini current --check-heads
full restart api worker web
full up -d --no-build --wait --wait-timeout 180
python3 "$release/infra/full-stack/smoke.py" \
  --base-url http://127.0.0.1:5190 --evidence-dir /absolute/path/to/private/smoke --existing
```

`--existing` reuses the owning session and confirms the saved result hash after
restart. `full logs --tail 100 graph-check runtime-check migrate api worker`
explains a startup failure without printing database passwords. Do not publish
expanded `full config` output because it contains credentials.

## Back up and restore

Save database and immutable HTTP observations together with the exact code
revision, private environment file, graph manifest, source hashes, original
artifact and deployment artifact hashes. Back up while no new jobs are being
submitted; stop the worker briefly if a consistent observation snapshot is
needed. A database dump alone does not contain the external graph or observations.

```sh
backup=/absolute/path/to/private/waydex.dump
(umask 077; full exec -T postgres pg_dump -U waydex -d waydex -Fc > "$backup")
test -s "$backup"
full run --rm --no-deps api tar -C /observations -czf - . > /absolute/path/to/private/observations.tgz
```

Validate a dump without replacing any live database by creating a fresh database
inside this isolated project's PostgreSQL container:

```sh
full exec -T postgres createdb -U waydex waydex_restore_check
full exec -T postgres pg_restore -U waydex --exit-on-error --no-owner \
  --dbname waydex_restore_check < "$backup"
full exec -T postgres psql -X -v ON_ERROR_STOP=1 -U waydex -d waydex_restore_check \
  -c 'SELECT count(*) FROM waydex_full.runs;'
```

For an actual recovery, use a fresh project and fresh volumes, restore the dump
into its PostgreSQL service before starting migrations/API/worker, and restore
the observations archive into that project's observation volume with ownership
`10001:10001`. Reuse the recorded artifact bindings and verified external graph.
Then run the migration head check and `smoke.py --existing` with the preserved
session. Keep the old project and volumes until recovery is verified. Routine
shutdown is `full down`; volume deletion is a separate explicit operator action.

## Extend the runtime

The API image contains the workspace source and all committed accepted-data
collections, while `.dockerignore` excludes `.env`, local dependencies, secrets,
raw data and graph artifacts. Rebuild from the integrated revision when new
runtime owners land changes. Optional `A2_REGISTRY_HOOK`, `A2_PREFLIGHT_HOOK` and
`A2_CALCULATION_HANDLER` variables can select reviewed product hooks. Place a
hash-pinned product manifest and its relative input files in the deployment
directory; configure `WAYDEX_PRODUCT_PATH=/runtime/manifest.json` and
`WAYDEX_PRODUCT_SHA256` identically for API and worker. The product digest is
included in the code fingerprint. Paid provider credentials are not part of this
profile. A hook change requires its own real acceptance checks.

`proof-builder.Dockerfile` is an optional isolated compiler image for the
engine-owned native source-bound verifier. It links the installed pinned
Valhalla library and never rebuilds or changes a running graph. The package
repository is used to install compilation tools; the resulting builder image
and native binary hashes must be recorded for proof artifacts.

```sh
docker build -f "$release/infra/full-stack/proof-builder.Dockerfile" \
  -t waydex-walk-proof-builder:3.5.1 "$release"
docker run --rm --memory 2g --cpus 2 \
  --mount "type=bind,source=$release,target=/source,readonly" \
  --mount type=bind,source=/absolute/path/to/new/proof-output,target=/output \
  waydex-walk-proof-builder:3.5.1 sh -ec \
  'g++ -std=c++17 -O2 $(pkg-config --cflags libvalhalla) /source/packages/engine/native/source_bound_walk.cc -o /output/source-bound-walk $(pkg-config --libs libvalhalla)'
```

This uses the pinned `libvalhalla.a` and the builder's shared dependency
libraries. Execute the resulting Linux binary in that same builder image;
record `ldd`, source/binary SHA256 and the builder image ID. Compile success is
not a route-minimum proof; follow the engine owner's actual export/replay checks.
