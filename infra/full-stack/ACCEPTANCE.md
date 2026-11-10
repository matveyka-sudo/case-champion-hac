# Local acceptance, 10 November 2026

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

Private logs, dumps, cookie session and JSON reports remain outside Git at
`/Users/kirito/waydex-full-runtime`. Do not publish the private environment file
or cookie jar. This acceptance covers the walk deployment profile; it does not
claim acceptance of later product hooks, native minimum proofs, paid providers,
public TLS hosting, or a graph rebuilt on a second machine.

The optional native helper also compiled successfully after installing the
missing ZeroMQ/date development headers. `pkg-config --libs libvalhalla`
resolved the pinned Valhalla archive and shared dependencies. Builder image ID
was `sha256:829fa84fde0e664a34fba73dc1c18e95a2a30309b6cf7ca4d3e45b5492439753`;
binary SHA256 was
`ce69e9e49e9779708948652bf8f6ab0b464e7fe29e27fa5905fa0f788298d35d`.
Actual native export/replay belongs to the engine acceptance, separately from
the deployment checks above.
