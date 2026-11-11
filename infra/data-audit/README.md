# Isolated native audit image

The frozen e5 API environment contains osmium 4.3.2, but its Debian slim runtime
does not contain `libexpat.so.1`. Importing osmium fails before a PBF is read.
This separate audit image installs only `libexpat1:arm64=2.5.0-1+deb12u5`.
It inherits the exact e5 Python environment without running pip/uv or changing
the API Dockerfile, production image tags, services, runtime inputs or volumes.

The [official Debian download record](https://packages.debian.org/bookworm/arm64/libexpat1/download)
pins the 91,748-byte package and SHA256. `build.py` checks both that pin and the
exact locally available e5 image ID, builds without network access, then checks
the base tag again and verifies that all base filesystem layers are preserved.
The recipe is arm64-specific and deliberately fails for a different base.

From this checkout, with the frozen e5 image already loaded:

```sh
python3 infra/data-audit/build.py \
  --output /absolute/new/private/audit-build-v1 \
  --tag waydex-data-audit:expat-e5-arm64-v1
python3 -m unittest discover -s infra/data-audit -p 'test_*.py'
```

The host downloads only the checksum-verified Debian package; Docker build uses
`--network=none`. Preserve the build receipt, log and exact image ID. Run audits
by that ID with `--network none`, `PROJ_NETWORK=OFF`, read-only source mounts,
an unprivileged user, a read-only root, bounded writable tmpfs and explicit CPU,
memory, swap and deadline limits. The image entrypoint is Python, not a server.

Real imports and a synthetic PBF/area/sparse-file-index smoke must pass before
using the image. The required import closure includes osmium, pyproj, Shapely,
pyarrow, numpy, rasterio, pydantic and the pinned audit module. Preserve actual
native PROJ/GEOS/GDAL versions, package inventory, cgroup peak/OOM evidence and
the smoke/source hashes. Module discovery alone does not exercise linkage.

The verified e5 environment has osmium 4.3.2, pyproj 3.8.0 (PROJ 9.8.1),
Shapely 2.2.0 (GEOS 3.14.1), pyarrow 26.0.1, numpy 2.5.4,
rasterio 1.5.2 (GDAL 3.12.2) and pydantic 2.14.3. The initial repair gate
compared all 8,799 regular `.venv` files (535,198,184 bytes) plus symlinks
against the base. The byte inventory was identical, and the complete dpkg
inventory had exactly one addition, with no removal or version change.

The initial smoke was supplied by ML2, SHA256
`872eeb317b7e3e9f6398490cb6221605d9cef9a52d91dd5cb0283a587e8426ef`.
It mounted audit module commit `f65890423ebf2c45df5cc09b8ec1b682ddbe7e57`,
file SHA256 `d201cffb91c0d1a92b871c4cbe22db57d115abad35b18e5190a6e31df4365bec`,
read-only. A 363-byte synthetic PBF exercised native reading, multipolygon
assembly with an inner hole, a disk-backed `sparse_file_array` index and
clear-in-hole versus blocked-in-water classification. With one CPU, 512 MiB
memory, no swap, network disabled and 64 MiB temporary storage, it passed in
0.556 seconds. Cgroup peak at smoke completion was 81,506,304 bytes, with all
OOM events zero. No original source data was mounted or processed.

Repairing linkage does not authorize an original-data scan. Every such scan
still needs a new reviewed immutable plan with the new image ID and unchanged
source/closure/resource/output pins. Preserve the prior failed container and
incomplete receipts; never add completion hashes to an incomplete audit.
