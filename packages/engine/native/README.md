# Source-bound pedestrian bounds

`source_bound_walk.cc` reads Valhalla tiles without changing them. Build it against
the headers and static library shipped in
`ghcr.io/valhalla/valhalla@sha256:4ac3d13d1019f9a130e9e6b6924c16ea55da0fa51e3653039152e33ad4095661`
(3.5.1). The isolated DEVOPS proof-builder image supplies the compiler and link
dependencies. Binary and execution artifacts belong outside Git.

```sh
g++ -std=c++17 -O2 $(pkg-config --cflags libvalhalla) source_bound_walk.cc \
  -o /proof/source-bound-walk $(pkg-config --libs libvalhalla)
```

The exporter enumerates `GraphReader::GetTileSet()` at every hierarchy level,
every node and every node-owned directed edge. Node-owned edge counts must equal
each tile header's directed-edge count; missing end tiles abort. It exports all
hierarchy node transitions with zero time. It excludes only shortcuts,
`!DynamicCost::IsAccessible`, and the three transit connection uses forbidden by
`PedestrianCost::Allowed`. Those are necessary conditions in the pinned source,
so every admissible pedestrian traversal survives. In particular it retains
conditional access, surface, hiking difficulty, distance-resource and turn-history
constraints in the relaxed domain. The pinned pedestrian `Allowed` rejects
shortcuts: enumerating all non-shortcut edges on all levels preserves its real
traversals, rather than assuming local-level edges alone are complete.

Every retained edge receives the actual library's `EdgeCost(...).secs`. The first
schema used zero turn lower bounds. Schema 2 computes each outgoing edge's minimum
`TransitionCost.secs` over every incoming edge ending at that node, including every
actual inverse hierarchy transition. Inadmissible predecessors remain included,
so the minimum is no greater than any admissible turn. The checker independently
reconstructs the predecessor counts from the complete edge/transition inventory.
Initial fragments and zero-length terminal fragments do not invent a turn.
The exporter records binary32 bits, not a decimal rendering. A
native `loki::Search` provides binary64 projection and split-edge percentages;
both its normal and filtered retry candidates are retained. Partial traversal
weights execute the library's multiplication with the same binary32 fraction
conversion as the pinned unidirectional algorithm. Same-edge direct travel is an
explicit alternative. The search domain is the **declared native endpoint set**.
Loki's finite spatial search is not an inventory of all physical entrances.

For the exact additive pinned-weight model, let `L` be the relaxed shortest path,
`T*` the admissible minimum in that same declared endpoint set, and `U` a replayed
feasible candidate. Nonnegative omitted turn weights and dropped restrictions
give `L <= T* <= U`. Replay checks directed continuity (including an actual
hierarchy transition), node access, `Allowed`, history-sensitive `Restricted`,
the distance resource and `TransitionCost.secs`; ferry candidates are rejected
for this pure-walk upper bound. Only `L == U` proves this scoped network minimum.
A replay rejection gives no upper bound. A positive gap is a bound, not a minimum.

This objective adds the exact values represented by individual binary32 weights.
It does not claim that Valhalla's floating accumulated clock or millisecond HTTP
summary equals that rational sum. Static `current_time=0` is an explicit pinned
model policy, not a claim about dated real-world access or live traffic. The
helper rejects dated requests, non-foot profiles and access/oneway overrides.

Physical connectors must be added once at each end. Physical access-to-native
projection offsets must be explicitly known, and the physical alternative roster
must be independently complete. The checker defaults both obligations to unknown.
Scalar connector/offset weights can describe the selected candidate's feasible
upper bound. They cannot shift a minimum over multiple alternatives: route A may
have network time 1 and access time 100, while B has network time 2 and access 0.
This helper always leaves the physical lower bound null and physical minimum false;
it requires a future solver with separately weighted physical alternatives before
any promotion. A complete-roster flag cannot discharge that obligation. Unknown
offsets supply no feasible physical upper bound. Six-decimal `/locate` output is
never promoted to an exact match. A full category/territory index also needs its
own complete destination/population inputs; this module cannot establish those.

The executor `examples/audit_source_bound_walk.py` verifies every original tile
checksum and exact tile-file roster, config/profile hashes and tile-directory
binding. Extract archives, remote/compressed/symlink tile sources are rejected.
Each execution reserves an exclusive `runs/<id>` directory, copies its own binary,
native/checker/executor source, compiler manifest and inputs, pins the builder image
ID and library hash, and preserves failed partial runs. Existing run IDs or files
are never overwritten. The receipt records the Git commit and file hashes with
`index: null`. Earlier prototype outputs written in place are not immutable
execution receipts; use a fresh run from this executor for review.

```sh
PYTHONPATH=packages/engine/src python packages/engine/examples/audit_source_bound_walk.py \
  --graph-dir /path/to/clean-graph \
  --output-dir /path/to/source-bound-walk \
  --artifact /path/to/walk-pilot-artifact.json \
  --run-id unique-review-id \
  --exact-turns
```

The expected source files under the output directory's `source/` come from the
3.5.1 tag: [pedestriancost.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/pedestriancost.cc),
[unidirectional_astar.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/unidirectional_astar.cc),
[search.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/loki/search.cc).
The checker verifies an executor receipt; arbitrary caller-supplied hashes do not
establish source authority. There is no production `WalkResponseEnvelope` adapter.

With `--exact-turns`, a feasible native incumbent authorizes an exact zero-turn
closed ball at `d <= U`. The checker independently constructs every immediate
last-edge/outgoing-edge pair at the same node or after one directed hierarchy
hop. The native pair mode validates actual adjacency and returns exact turn
weights with a source-scope/plan header. All terminal fragments are absorbing
alternatives even when their full edge ends outside the ball; direct same-edge
paths and zero source fragments are handled separately. Exact edge-state search
retains zero-cost, positive-fraction history and zero cycles. A final successful
native replay is required before a scoped network minimum is reported. A failed,
modified or foreign-source/profile incumbent cannot authorize the cut.

The executed pilot's network optimum was `1088850011/2097152` seconds, with 794
ball nodes, 1927 states and 5640 complete turn pairs. Native actor extraction
(`observed_route.cc`) confirmed the same positive-fraction edge path as the
519.204-second HTTP route. Native floating accumulation differs from the exact
additive optimum by `37/2097152` seconds; that clock is outside this exact-sum claim.
The complete physical alternative weights/coverage remain unbound, so physical
minimum and category index remain unavailable.

## Geometry for an already certified path

`export_certified_geometry.cc` reads the exact directed traversal sequence from
the preserved certificate. It never invokes a router, Loki search or HTTP. Build
against the same pinned builder/library with `-ffp-contract=off`; this preserves
the operation-by-operation binary64 projection replay in the Python checker.
The existing proof binary and receipts must remain unchanged.

`urban.walk.geometry.make_certified_geometry_plan` cross-binds the original native
receipt, candidate file, query, complete source binding and selected endpoint
bits. `load_certified_walk_geometry` checks an attested native export against that
plan, the complete verified graph and the certified candidate. Every row carries
its ordinal, directed edge ID, traversal fraction, source node IDs, OSM way ID,
actual `DirectedEdge.forward()` flag, raw shape bits and raw node coordinate bits.
The flag read by the pinned native exporter is the direction authority.

The installed `encoded.h` defaults to `ENCODE_PRECISION=1e6` and
`DECODE_PRECISION=1e-6`; `Shape7Decoder` is the codec name, not a promise of seven
coordinate decimals. `NodeInfo::latlng` reconstructs a separate seventh digit.
Both representations are preserved. The node-lattice check is a necessary sanity
check only: a decimal half-microdegree can have either adjacent sixth-digit
representation because original and reconstructed doubles differ. For example,
the actual airport node longitude `20.5290215` has shape longitude
`20.529021` even though re-encoding the reconstructed double chooses
`20.529021999999998`. This check never moves a vertex and is not a standalone
direction or connectivity proof. The compiler manifest preserves the exact
headers and their hashes; their source equations are in `encoded.h:8-20,36-39,
197-198` and `nodeinfo.h:69-72`.

First/last clipping uses the recorded Loki projection bits and the actual stored
shape segment. It does not multiply the percentage by shape length: Loki 3.5.1's
percentage uses the integer edge length. Native `projector_t` scans the raw storage
order with strict `<` comparison. The checker repeats that arithmetic using the
attested native libm operands, including first-segment tie resolution at
self-crossings. Shapes are reversed only by the directed edge flag. The network
LineString is available only when all adjacent directed shape endpoints are
bit-equal; otherwise components remain separate and the exact gap positions are
reported. No gap receives a fabricated connecting line.

Full model geometry additionally requires the previously reviewed physical
receipt. It uses only the selected alternatives' existing physical-to-canonical
and canonical-to-native straight-link geometries, reversing both destination
links, and requires exact joins. These links retain their canonical-access model
limitation. Geometry does not establish a new minimum, entrance roster, category
coverage, or index. The ordinary airport actor path has 92 edges and cannot be
used for the certified 97-edge path.

```sh
PYTHONPATH=packages/engine/src python packages/engine/examples/export_certified_walk_geometry.py \
  --model-dir /path/to/certified-walk-model-v1 \
  --graph-dir /path/to/clean-graph \
  --assets-dir /path/to/isolated-geometry-build \
  --output-dir /path/to/isolated-geometry-build \
  --run-id unique-reviewed-run \
  --model-receipt /path/to/airport-physical-final-20261110-v4/receipt.json \
  --model-receipt-sha256 e9bbfca53148acbb6997bbefe39bcc045a080086874fb1766e84c65d79040373
```

The executor verifies all original certificate files and the graph before and
after the read, disallows alternative GraphReader sources, and creates an
exclusive run directory. Native binary/source, exact headers, checker, plan and
original receipt copies are retained with hashes; failed partial runs remain
preserved. The supplement receipt binds the current proof-binding (including
target snapshot compatibility), original native/model/physical receipts, all
source tile hashes and exact sequence. Source authority is established by the
reviewed native execution and deployment registry, not self-selected hashes.

## Versioned route endpoint echoes

`valhalla-3.5.1-route-echo-trunc6-v1` is an optional existing
`serialization_evidence_refs` entry. It permits only the supported deterministic
six-place echo of the original source coordinate when the build is 3.5.1 and the
correlation already carries six-place locate evidence. It changes neither the
source request nor the separately attested correlated endpoint. Correlation
half-quantum checks and minimum-proof restrictions remain unchanged.

The 3.5.1 `src/tyr/route_serializer_valhalla.cc` `locations()` sets precision6 and
writes `location->ll()`. Source SHA256:
`8a704ca51b9df32f8a491cd022a375935f306c1a36b2ce8c81fbec57bf325217`.
The pinned `baldr/rapidjson_utils.h` forwards this to `SetMaxDecimalPlaces`
(SHA256 `30aeb5b7d2d678f0834a12577b7ae6e1b9cfcfb36460e1e3d228102e96c99505`).
Pinned RapidJSON `internal/dtoa.h` `Prettify` truncates decimal digits
(SHA256 `73bdbedab68a0647156c918a7c879b7b0f13b88e2de2145c05fd7c65c8970cc5`).
This differs from `/locate`'s `fixed_t` rounded projection.

The validator uses decimal source digits, not binary multiplication by1e6.
Negative truncation is toward zero and tiny signed zero is preserved. The native
scientific-notation decade near1e-7 is handled separately. Noninteger inputs
already at six decimal places receive no new echo alternative: Grisu2 can choose
a longer decimal across that boundary. Their previous exact/locate acceptance
remains available. This conservative subset avoids a radius or rounding tolerance.
An offline pinned-writer oracle covered40644 values;29615 supported values agreed
bit-for-bit, and11029 boundary inputs were excluded from the new alternative.
