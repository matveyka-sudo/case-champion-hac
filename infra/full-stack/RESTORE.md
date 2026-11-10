# Restore the certified runtime outside Git

Use the approved source commit and operator manifests. Preserve the native
receipt, original `.3` artifact, physical access receipt, `.3` to `.5`
compatibility record, canonical access policy and dated official sources.
Packaging these bytes adds no numerical proof. The worker recomputes the native,
turn and physical certificate; it does not replace the native candidate with an
HTTP actor path.

## Produce the private archive

Create an explicit allowlist specification, rather than copying a directory:

```json
{
  "schema_version": "waydex-runtime-bundle-inputs/1",
  "files": [{
    "path": "native/receipt.json",
    "source": "/absolute/path/to/approved/native-run/receipt.json",
    "bytes": 39071,
    "sha256": "e6cef13a2e3681f5cf0d337ae95c13ff292977e7a7ec978bb9fa13a38aca292b",
    "executable": false
  }]
}
```

This record illustrates the format; a complete deployment also includes:

- Final product/proof manifests and the referenced canonical `.5` snapshot.
- All 22 native-indexed files plus the native receipt.
- Six copied physical proof files plus the physical result receipt.
- Compatibility, canonical-access policy and official-source manifests.
- Eight exact official captures and the pinned upstream source inventory.
- Every graph tile, build manifest, PBF, configuration/profile, configuration
  generator, serializer and version record.
- These instructions and any other explicitly reviewed source materials.

Put operator manifests at the bundle root and use relative FileRefs such as
`native/receipt.json` and `metadata/compatibility.json`. Preserve historical
receipt fields such as `native_run`; loaders must not follow the original
machine paths. Producer `source` fields are stripped from the bundle manifest.

```sh
python3 infra/full-stack/bundle.py pack \
  --spec /absolute/path/to/private/bundle-inputs.json \
  --output /absolute/path/to/private/approved-runtime.tar.gz
```

Record the printed archive SHA256/size, file-manifest SHA256, file count and
uncompressed size. Sorted files, fixed metadata and a zero gzip timestamp make
identical approved inputs reproducible. Existing output is never overwritten.
Private environment/Git/credential paths are rejected. Bounds are 128 MiB per
file, 4096 files and 512 MiB total. Changed source bytes fail publication.

For the optional geometry release, create a new archive and restoration directory.
Preserve the original archive, binding and running projects. Add the reviewed
`geometry-supplement.json`, all 32 files indexed by its geometry receipt and the
receipt itself under `geometry/`. Include its native helper binary, source,
compiler manifest and every indexed header, including headers unused by the
display path. Keep these files in their namespace: `geometry/export.jsonl` and
`native/export.jsonl` have different purposes and hashes.

The new proof binding adds only an optional `geometry` FileRef to the supplement.
The supplement has schema `waydex-certified-walk-geometry-binding/1` and two
FileRefs, `receipt` and `original_snapshot`. Its receipt points to
`geometry/receipt.json`; the original snapshot points to the unchanged `.5`
`accepted-data.json`. Preserve the original product manifest and runtime settings
under `provenance/original-product-manifest.json` and
`provenance/original-runtime-settings.json`. Preserve the original binding bytes
in `geometry/proof-binding.json`; the new binding's fields excluding `geometry`
must equal that original binding. Historical paths in copied receipts remain
inert metadata.

The reviewed geometry receipt SHA256 is
`89dcd9238cf1289e243030611303bb2a964b819a978e51aba9bf83a2045a4eab`.
Its copied original binding remains
`d52715d0617d53895db04f22695c055667c6c1a3abd250c13c0f5eb200cd0cd2`.
The original snapshot FileRef uses the raw file SHA256
`f616122d81a297917d49fa64bec39a9876c20ef385469a3f4a7cbdf9b271b82d`;
its API canonical identity is separately
`e3ac08680e52b9938d4f816690aefd6d15054ccf308cbc780918b4c8d61e4930`.
The engine compatibility gate retains its separate ASCII canonical convention.
Do not substitute canonical identities for FileRef byte hashes.

Startup first verifies the complete restored inventory, then every geometry and
native source hash, original binding semantics and compiler/header provenance.
The geometry inventory is bounded to 64 files and 16 MiB total; local references
reject links and path escapes. The backend separately validates the canonical
snapshot and recomputes the source-bound geometry with the numerical proof.
Omitting `geometry` keeps the previous runtime compatible. Geometry adds a
display line; it does not change the exact index operand, coverage or ranking
eligibility.

Keep the archive private and outside Git. Uploading it requires an authorized
destination. Current URL responses cannot replace historical captured bytes or
their dated evidence.

## Restore on the recipient machine

Obtain the archive and both exact hashes through the approved handoff:

```sh
python3 infra/full-stack/bundle.py restore \
  --archive /absolute/path/to/private/approved-runtime.tar.gz \
  --sha256 RECORDED_ARCHIVE_SHA256 \
  --manifest-sha256 RECORDED_FILE_MANIFEST_SHA256 \
  --output /absolute/path/to/new/restored-runtime
python3 infra/full-stack/bundle.py verify \
  --directory /absolute/path/to/new/restored-runtime \
  --manifest-sha256 RECORDED_FILE_MANIFEST_SHA256
```

Restore verifies archive/manifest hashes before creating a new destination. It
rejects links, path escapes, duplicate/unlisted members, missing files and
size/checksum changes. Files are created exclusively and become read-only;
executables retain execution permission. The completion record appears only
after every declared file verifies. Failed partial directories remain available
for diagnosis and cannot pass the startup bundle check.

## Start the certified stack

Copy `.env.certified.example` to a private file outside the release and restored
bundle, mode `600`. Set the exact approved source revision and manifest hashes.
`WAYDEX_DEPLOYMENT_DIR` is the restored root; `WAYDEX_GRAPH_DIR` is its `graph`
subdirectory. The geometry template uses a fresh `waydex-full-geometry-1110`
project, new volumes and port 5193. Preserve the 5190/5191/5192 projects and their
environment files. Build only after the source commit and new private archive
hashes have been approved.

```sh
certified() {
  docker compose --env-file /absolute/path/to/private/certified.env \
    -f /absolute/path/to/clean/release/infra/full-stack/compose.product.yaml \
    -f /absolute/path/to/clean/release/infra/full-stack/compose.product-certified.yaml "$@"
}
certified config --quiet
certified build api web
certified up -d --no-build --wait --wait-timeout 180
certified run --rm --no-deps migrate /app/.venv/bin/alembic \
  -c /app/apps/api/alembic.ini current --check-heads
```

The checker validates the restored inventory, private binding and actual mounted
graph/configuration/profile against the native proof. It performs no school
smoke or HTTP actor call. Migrations finish before API/worker start. Batch
activation has a separate gate; creating migration `0005` does not enable it.

Submit the owner's approved dated airport request, verify the canonical point
score and scope, then verify it through the browser. Preserve the owning session
for restart checks. Unproven city aggregates and other points retain their
partial/unavailable semantics.

The worker starts with a 1 GiB bound. If measured execution needs the approved
2 GiB ceiling, set `WAYDEX_WORKER_MEMORY=2g` only in this project's environment
and explicitly rerun the failed work. Record actual cgroup peak/limit/OOM events,
which include the supervisor and spawned child:

```sh
certified exec -T worker cat /sys/fs/cgroup/memory.peak
certified exec -T worker cat /sys/fs/cgroup/memory.max
certified exec -T worker cat /sys/fs/cgroup/memory.events
```

Current `docker stats` memory is not a peak. Keep startup logs, job/result hashes,
timing and restart evidence with the release. The database/observation restoration
procedure in [README.md](README.md) also includes the new checkpoint tables.
