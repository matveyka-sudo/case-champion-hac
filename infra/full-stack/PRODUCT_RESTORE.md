# Restore and run the current three-city product

Use a clean checkout of the reviewed source commit, Python 3.12 and Docker with
Compose v2. Keep datasets, filled environment files and credentials outside Git.
The original ML2 ZIP is an ingestion source; it is not a complete runnable release.
Obtain these separate artifacts from the release operator:

- Runtime archive with its archive SHA256 and bundle-manifest SHA256.
- Complete graph transfer tree, including `graphs/{moscow,novorossiysk,kaliningrad}`,
  `bindings/{moscow,novorossiysk}` and `graph-transfer-manifest.json`.
- Reviewed `waydex-runtime-transfer-pins/1` JSON and its SHA256, supplied separately
  from the files being verified. Do not generate replacement expected hashes on
  the target to make an unverified archive pass.

The runtime contains accepted inputs, immutable supplements, graph metadata and
the native certificate. Graph transfers contain the actual OSM/tile/config files
and operator provenance bindings. Route observations belong to a separate Docker
volume; copy that volume and PostgreSQL when migrating saved history. A fresh
database needs no old calculation results. Connected urban walking and truck
profiles calculate missing route observations in the worker on request. Static
intercity car profiles currently consume prepared observations from the runtime;
arbitrary intercity routing is not yet connected. Missing catalogue objects and
physical connections are not invented.

Set shell variables to the actual checked release values; all paths below are
absolute. No path from the original developer's Desktop is required.

```sh
python3.12 "$source/infra/full-stack/bundle.py" restore \
  --archive "$archive" --sha256 "$archive_sha" \
  --manifest-sha256 "$bundle_sha" --output "$runtime"
python3.12 "$source/infra/full-stack/four_profile_restore.py" verify \
  --runtime "$runtime" --transfer "$transfer" \
  --release-pins "$pins" --release-pins-sha256 "$pins_sha"
```

The exporter supports the same optional release pins when making the source
transfer. Existing four-profile pins remain unchanged when the flags are absent.
All three router directory timestamps are preserved for the new release. If
transporting the tree as a tar archive, preserve directory metadata; re-run the
verifier after extraction. Export graph-only directories; private historical
`observations/` files must travel as evidence separately, not inside graph trees.

Build from the pinned clean checkout, then obtain the executor fingerprint from
that exact image. The fingerprint command does not call providers.

```sh
revision=$(git -C "$source" rev-parse HEAD)
docker build -f "$source/infra/full-stack/api.Dockerfile" \
  -t "waydex-full-api:$revision" "$source"
docker build -f "$source/infra/full-stack/web.Dockerfile" \
  -t "waydex-full-web:$revision" "$source"
executor_sha=$(docker run --rm --network none \
  --entrypoint /app/.venv/bin/python "waydex-full-api:$revision" \
  -c 'from a2_api.adapters.walk_runtime import executor_fingerprint; print(executor_fingerprint())')
python3.12 "$source/infra/full-stack/product_env.py" \
  --source "$source" --revision "$revision" --executor-sha256 "$executor_sha" \
  --runtime "$runtime" --transfer "$transfer" \
  --release-pins "$pins" --release-pins-sha256 "$pins_sha" \
  --project waydex-product --port 5294 --output "$private/product.env"
```

The environment generator verifies inputs, derives graph/proof paths and hashes,
and creates a random database password in a new mode-0600 file. It refuses to
overwrite an existing environment. Keep this file for subsequent starts; changing
the password alone does not change an existing PostgreSQL volume's password.

Start the complete available stack with one command:

```sh
"$source/infra/full-stack/product-compose.sh" \
  --env-file "$private/product.env" up -d --no-build --wait
```

This creates one project network, PostgreSQL, three graph checkers and routers,
runtime validation, migrations, API, worker, and React reverse proxy. No external
Docker network or pre-existing Kaliningrad container is required. The URL is
`http://127.0.0.1:5294/#atlas`. HTTP binds to loopback; public deployment requires
the server's HTTPS reverse proxy and secure-cookie configuration.

Check service logs with the same wrapper and environment. Do not use `down -v`
on a deployment whose results must be preserved. Large graph/data files remain
read-only mounts; routing cache/evidence and PostgreSQL use project-owned volumes.

Alice is disabled by this clean-start configuration. Existing analytics remain
usable. Live Alice additionally needs private provider credentials, server-side
budget state and explicit opt-in settings; this restore flow grants no new paid
budget. It does not imply nationwide coverage, complete city indices, accepted
intercity freight operations or comparable pipeline measurements.

The native certificate artifact must match the container architecture. A replay
on Docker Linux ARM64 does not establish AMD64 compatibility or a remote-server
deployment; record those gates separately.

## Verified isolated replay, 2026-11-11

Source `c782e20032b8a7a3a9069d3b870bc2d7a1c824a3` was built from a clean
detached checkout. The runtime archive and all five graph/binding transfer groups
were restored to a new directory. A new project network, PostgreSQL volume and
observation volume were used; existing stand containers remained unchanged.
All bind mounts pointed to the restored tree, and all three Valhalla instances
reported version 3.5.1 with their pinned tile timestamps. Startup took 91.298 s
after image builds. This used an existing Docker host with cached base images,
Linux ARM64 application containers and the configured AMD64 PostgreSQL image.

Before the browser request, PostgreSQL contained zero jobs and zero runs. A real
React request for outbound nonfood container freight in Novorossiysk created job
`9dee75a1-282d-4c0d-8f76-ab844e62e474`. The worker calculated 172 network times
in 39.111 s and published analysis `bc1731a4-0d2e-4d3d-9f75-243dbb27347f`.
React displayed the persisted result and 88 map features. Canonical validation
passed; repeat GET was byte-identical and an unrelated session received 404.
The 484826-byte result had SHA256
`beb21e20b687ef627dd82ef99ed9fbb88650787d6da9abc17f81a561d975deff`.
No operator precomputation command or paid provider was used.

This proves the isolated restore and one real partial freight flow, not every
profile or a deployment to a remote server. Strict freight indices remain null:
approaches, compatibility and complete catalogue/minimum proof remain unknown.
Live Alice was disabled in this replay. AMD64 native-certificate execution,
public HTTPS deployment and remote artifact distribution remain separate gates.
