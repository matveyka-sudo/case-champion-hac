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

Every retained edge receives the actual library's `EdgeCost(...).secs`; turns
receive zero. The exporter records binary32 bits, not a decimal rendering. A
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
An incomplete physical roster supplies no global physical lower bound. Unknown
offsets supply no feasible physical upper bound. Six-decimal `/locate` output is
never promoted to an exact match. A full category/territory index also needs its
own complete destination/population inputs; this module cannot establish those.

The executor `examples/audit_source_bound_walk.py` verifies every original tile
checksum and the exact tile-file roster, pins source-file hashes, records the
exporter source, binary, request, artifact, graph manifest and output hashes,
executes export and replay, then writes a receipt with `index: null`.

```sh
PYTHONPATH=packages/engine/src python packages/engine/examples/audit_source_bound_walk.py \
  --graph-dir /path/to/clean-graph \
  --output-dir /path/to/source-bound-walk \
  --artifact /path/to/walk-pilot-artifact.json
```

The expected source files under the output directory's `source/` come from the
3.5.1 tag: [pedestriancost.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/pedestriancost.cc),
[unidirectional_astar.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/unidirectional_astar.cc),
[search.cc](https://github.com/valhalla/valhalla/blob/3.5.1/src/loki/search.cc).
The checker verifies an executor receipt; arbitrary caller-supplied hashes do not
establish source authority. There is no production `WalkResponseEnvelope` adapter.
