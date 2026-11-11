# Certified one-point deployment, 10 November 2026

The isolated release at `http://127.0.0.1:5192` was built from a clean
`git archive` of `8fc49fe5607f3077c22771c8a9debb50ee4f6e02`. It uses project
`waydex-full-index-1110`, fresh PostgreSQL/observation volumes and the certified
overlay. Existing 5190/5191 projects, original proof runs and graph were preserved.

The enabled profile is Kaliningrad / walking / static / airport / 10 November,
using the declared canonical-access model for original point
`kaliningrad-utm34n-1km-v1-e468-n6069-p`. The accepted `.5` snapshot retains all
216 sample points and their original population weights.

| Release input | SHA256 |
| --- | --- |
| Product manifest | `753efeeeff293196e56208faa8d082c401922167924ff505a51fb768220aa546` |
| Certified proof binding | `d52715d0617d53895db04f22695c055667c6c1a3abd250c13c0f5eb200cd0cd2` |
| Native receipt | `e6cef13a2e3681f5cf0d337ae95c13ff292977e7a7ec978bb9fa13a38aca292b` |
| Restored file manifest | `b388b20049581cecfc7f072437068173b86ff2eec116b1c720ab2a60559134b8` |
| Private archive | `663ca5df8591b25bb47ccce418baad9893d9e036b9d1c56401bc4f1365b5635b` |
| API image | `ef1415ef76049fe1d7cf009a9732f027289504ab6fa864330e2fc6a8f1600529` |
| Web image | `2de08a111b291a0d1a7da1d8cc036a3afa541626b723d62ee19c24ab276bb080` |

The private archive is 109,685,687 bytes. Its 189 allowlisted files contain
239,768,262 uncompressed bytes: complete native/physical evidence, original
artifacts, graph/PBF, official captures, policy/compatibility records, accepted
snapshot, manifests and receipt-bound producer sources. The exact older
`build_city.py` was recovered from Git commit `9c5f7cf` by its recorded checksum.
Source-machine paths were stripped from the packaging manifest; historical
receipt metadata and all input JSON bytes remain unchanged. There was no upload.

| Check performed by DEVOPS | Observed result |
| --- | --- |
| Clean source builds | API 30.621 s; web 20.536 s; frozen locks and pinned base images |
| Complete bundle restore/verification | Passed; exclusive destination, every size/SHA checked |
| Restored proof + actual mounted graph/config/profile checks | Passed; no HTTP actor call |
| Full readiness including fresh PostgreSQL | 18.882 s; six services only |
| Database | PostGIS 3.7.0; `0005_od_checkpoints (head)`; all three OD tables present |
| Four actual-container negative checks | Wrong proof SHA, wrong inventory SHA, missing proof and missing graph rejected |
| Real API → product dispatcher → spawned worker | Succeeded in 17.526 s |
| Focused saved-result explanation | `template`, `DISABLED`, no model attribution, matching saved hash |
| API/worker/web restart | Passed; owning-session retrieval and both persisted records unchanged |
| Database dump/restore into new check database | 90,208-byte dump; same saved result hash; 1.610 s |
| Infrastructure controls | 19 tests, Ruff and certified Compose assertions passed |

The DEVOPS job was `a65ea917-ca39-43c4-8ad2-af3131461ebd`, analysis
`6fba7076-8990-4c42-81b3-c80a32a769fc`, result hash
`710f5d4502d15327dc92d6d0c31a42630b3928ed440494fcff5ba32c187d871a`.
Its category and point indices were both `0.4971801994143471`; the computed model
duration was `13773.627798430474` seconds. Exactly two of 433 scores were numeric,
both for that original point. The city score remained null and ineligible for
ranking; analysis status remained partial. All 216 point map features remained.
No journey geometry was invented: the result explicitly reports
`CERTIFIED_MODEL_DURATION_ONLY_NO_ROUTE_GEOMETRY`.

The worker's first measured cgroup peak was 1,004,261,376 bytes under a
1,073,741,824-byte limit. After the root coordinator's separate browser job, the
peak was 1,009,315,840 bytes. `max`, `oom` and `oom_kill` counters were all zero;
Docker reported no OOM and no spontaneous restart. No 2 GiB increase was needed
for these two executions. These are observed profile measurements, not a capacity
claim for larger graphs or batches.

The root coordinator separately reported a browser-created job
`46c9aba0-cdd2-499d-8ac0-48df59f7b453`, analysis
`5bf81f85-5787-495b-bee0-8bbc8d4e04a0`, with the same numeric point/category values.
DEVOPS subsequently confirmed that its persisted hash
`b28fa99fab0123d0c0cfb46dfcdd55e21cb68258a7940dadd5bc76d3c40baefe`
survived the authorized restart. That browser execution is the coordinator's
independent check, separate from the DEVOPS API job.

Private archive, restored files, environment, cookie jars, resource samples and
logs remain under `/Users/kirito/waydex-full-runtime`; none were committed.
The stack remains available after restart. Batch execution, scenario documents
and Rasp providers were not enabled in this profile. Migration 0006/quota work
has a separate review/deployment gate; this release remains on tested head 0005.
