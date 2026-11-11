# Local acceptance, 10 November 2026

The later certified point release on 5192 is recorded in
[CERTIFIED_ACCEPTANCE.md](CERTIFIED_ACCEPTANCE.md). The checks below preserve
the earlier walking pilot and generic dispatcher acceptance history.

The checked release was exported with `git archive` from
`f2f135a5aef55330d4babc56025daabb4ba2c283` (implementation `d27425a` plus the
Valhalla tmpfs correction). The final image build used that clean committed
source, frozen Python/npm locks and the image digests in this directory.
No local `.env`, `.venv` or `node_modules` was present in the export.

The isolated project was `waydex-full-devops-1110`, HTTP
`127.0.0.1:5190`, with new PostgreSQL and observation volumes. The existing
phase-2/baseline Valhalla and PostgreSQL containers were preserved.

| Check | Actual result |
| --- | --- |
| `docker compose config --quiet` | Passed |
| Python Ruff check/format, `git diff --check` | Passed |
| Image build from clean export | Passed; 94.319 s, including fresh Linux Python dependencies; corrected release retag/cache build 1.751 s |
| Pinned PostGIS pull | Passed; 234.070 s |
| Source graph, complete tile inventory, binary/image/source/config/profile hashes | Passed |
| Immutable derivative endpoint binding | Passed; original parent unchanged; second creation rejected |
| Wrong parent hash, wrong graph, missing graph | Rejected before derivative publication |
| Actual-container wrong parent hash, missing graph, wrong derivative hash | Rejected |
| Full startup and health/readiness | Passed; 16.041 s after PostgreSQL was separately initialized |
| Live source-bound status/locate/foot route before migration | Passed; network 519.204 s, total 547.404 s, 76 blocked pairs, minimum unproven and ranking disabled |
| Alembic `current --check-heads` | `0004_alice_budget (head)` |
| PostGIS SQL | PostGIS 3.7.0, spatial point `POINT(20.5 54.7)` |
| Real Nginx → API → queued worker job | Succeeded; 1.890 s; one journey, partial result, every score unknown |
| API/worker/web restart and owning-session retrieval | Identical persisted result hash; 0.113 s |
| `pg_dump -Fc` → fresh `waydex_restore_check` → `pg_restore --exit-on-error` | Passed; 51,089-byte dump; identical one saved run/result hash; 1.933 s |
| Observation archive | Created; 3,301 bytes |

Parent artifact SHA256:
`a7a09b0576041ab70a9f40a59e4df5e5602b30c3f7c5ce35b4ede779b7e1a72d`.
Deployment derivative SHA256:
`2c8972516a7e546a265e4a76a6e8006fceb2f1700a08787b1e8821f8a0924cb5`.
Observed analysis result SHA256:
`fbc7c07ac83b6ffdadaab19f8f1a98ccf555c51cca0e42b118edfee56a7300c8`.

Private logs, dumps, cookie sessions and JSON reports remain outside Git at
`/Users/kirito/waydex-full-runtime`. Do not publish the private environment file
or cookie jars. The initial checks cover the walk deployment profile. Product
dispatch acceptance is recorded below. Native minimum proofs, paid providers,
public TLS hosting and a graph rebuilt on a second machine have separate gates.

The optional native helper also compiled successfully after installing the
missing ZeroMQ/date development headers. `pkg-config --libs libvalhalla`
resolved the pinned Valhalla archive and shared dependencies. Builder image ID
was `sha256:829fa84fde0e664a34fba73dc1c18e95a2a30309b6cf7ca4d3e45b5492439753`;
binary SHA256 was
`67b42bb7e7e9c98e992d895a5c00579740b5a033b786f719145c3e702e574994`
after the engine owner's schema-2 turn-bound extension. Its source SHA256
was `53e53f311a3a9c94f87167433496bf3c8afd11c757a4c258d161a409a6bdfce4`.
Actual native export/replay belongs to the engine acceptance, separately from
the deployment checks above.

The generic `compose.product.yaml` was also rendered with the graph-free
`.env.product.example`: exactly six services, no Valhalla/graph-check, no walk
artifact environment and only runtime/observation mounts. Rendering the optional
walk overlay confirmed that both fingerprints reach API/worker, the runtime
checker receives the physical graph/parent mounts, and migration ordering is
retained.

## Unified product dispatch

A second clean `git archive` of reviewed integrated source
`8ee59ff1d4f89b04f0d515b4fc48d256313d437a` built successfully in 24.232 s.
Project `waydex-full-product-1110` used new volumes and loopback port 5191,
preserving the original 5190 project. Its accepted one-profile manifest SHA256
was `740fe3ff685b12daca3d9ebad9d719f7bea7994b5a8c00f62706297087a0ae73`.
The exact existing walk derivative was exclusively copied into the manifest
directory. No new dataset or route proof was introduced.

| Check | Actual result |
| --- | --- |
| Product checker + source-bound graph/live walk + migrations + full readiness | Passed; 18.958 s including fresh PostGIS initialization |
| Product hooks dispatch to real walk executor through Nginx/API/worker | Passed; job `71670d40-d779-4221-a0c3-2c201b10b6e8`, 2.376 s |
| Saved analysis | `f1804c26-1393-46c3-bb6d-60a370420b77`; one observed journey, partial, every score unknown |
| API/worker/web restart + owning-session retrieval | Identical saved hash; 0.128 s |
| Explanation endpoint | Template, `reason_code=DISABLED`, no model attribution, exact saved result hash |
| Actual-container wrong product SHA and absent manifest | Rejected |

Saved result SHA256:
`5c94f31dbd0f5c000802a9929ec4bc1d872652c941e7f7ec1ef7f66dd1efb788`.
This verifies unified dispatch for the accepted walking profile. Other product
scopes need their own accepted input manifest and real execution checks.

## Graph source precedence and packaging

The directory graph guard now rejects configured remote tile fallback and any
present configured tile archive, including a dangling symlink. Six focused
source-precedence tests pass. An actual copy of the pinned graph plus an added
`tiles.tar` still passed the older tile inventory checker, then was rejected by
the new guard. The original graph/config/build manifest were untouched and its
configured `/data/tiles.tar` remains absent.

With pinned uv 0.12.17, the data-pipeline manifest/lock update adds only the
contracts/engine workspace edges and changes no third-party version. A fresh
package-only frozen sync imported the approved `gtfs_inputs` module and ran its
CLI help with `a2-api` absent. Replacing all three workspace editables with built
wheels passed the same import/help smoke from installed wheel paths.
