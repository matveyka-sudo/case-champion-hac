# Exact finite walk oracle and the real proof boundary

`urban/walk/finite_oracle.py` is independent reference/test support. It computes
an elapsed-time minimum in a supplied finite state graph and checks candidates
against that minimum. It does not certify a real Valhalla graph, set a production
`minimum_proven` flag, authorize an available index, or change approved formulas.
All executable examples in `test_finite_walk_oracle.py` are synthetic.

## The objective and domain

A physical origin-to-destination route has exactly one initial connector, zero
or more directed network transitions, and exactly one final connector. Its time is
the initial connector seconds plus the sum of edge elapsed seconds and transition
elapsed seconds, plus the final connector seconds. These are seconds throughout;
distance, generalized cost, correlation score, and transition penalties without
elapsed time cannot substitute for elapsed seconds.

The graph must represent **all alternatives in the stated physical access scope**.
Choosing one confirmed connector proves its feasibility, not that another entrance
or graph correlation cannot yield a faster physical route. A result restricted to
one connector domain must retain that restriction. It cannot silently become a
minimum over every physically admissible entrance or all schools serving a city.

State IDs encode every predecessor/history/resource value affecting admissibility
or elapsed cost. Distinct arrival histories at the same geographic junction may
need distinct states. Access positions inside directed edges need explicit split
segments, including partial-edge traversal and the direct same-edge route. Initial
and terminal network segments and their transition costs must remain in the network
arcs, rather than disappear into an unverified coordinate match. Final connectors
may attach to multiple history states, each with independently correct admissibility.

The oracle accepts only static, finite, nonnegative `Fraction` seconds. It uses exact
rational arithmetic, no epsilon, hidden float conversion, or intermediate rounding.
For decimal input use `Fraction("9.7")`; for a source binary32 value retain its exact
binary value and convert that value to a rational. A rounded decimal representation
is a different weight. Exact sums of source weights define this oracle's objective;
they do not reproduce every intermediate float rounding of a Valhalla route label.
The source model, precision policy, connector units, and any source-rounding bound
must be explicit before comparing a real route with the oracle.

## Interface and checks

Import directly from `a2_engine.urban.walk.finite_oracle`:

| Type or function | Purpose |
| --- | --- |
| `FiniteWalkArc` | Stable ID, directed from/to state, separate exact edge and transition seconds |
| `FiniteWalkConnector` | Stable connector ID, physical endpoint ID, exact network state, exact seconds; serialized coordinate precision fails closed |
| `FiniteWalkGraph` | Pinned scope reference, physical origin/destination, explicit state/arc/initial/final connector inventories |
| `FiniteWalkInventory` | Separately retained complete ID rosters, content SHA-256, scope and evidence references |
| `finite_walk_graph_digest` | Order-independent digest binding scope, physical endpoints, states, directed arcs, connector alternatives, precision and all exact weights |
| `solve_finite_walk_graph` | Exhaustive exact multi-source Dijkstra, accounting for final connectors before selecting the physical minimum |
| `verify_finite_walk_candidate` | Directed path continuity, both physical endpoints, exact full elapsed total, and equality to the reference minimum |

The solver rejects mismatched scope, duplicate IDs, missing states/arcs/connectors
relative to the retained inventory, content changes, dangling states, empty endpoint
alternatives, physical endpoint mismatch, and endpoints with serialization uncertainty.
It accepts alternative tied paths, zero weights and cycles. Equal network states still
require both physical connectors. Identical physical endpoint IDs admit a zero stay
path after input verification. The solver represents this stay with both connector
IDs `None`, an empty arc tuple, and zero seconds; the candidate verifier accepts that
same representation. It rejects a single missing connector, nonzero stay time,
network arcs combined with omitted connectors, or omitted connectors for distinct
physical endpoint IDs. Explicit connector paths still require their full elapsed
total and must attain the minimum, including when the physical IDs coincide.
An unreachable result means unreachable **only in the
supplied inventory**; it is not a global no-route claim.

The inventory's rosters and hash detect an export truncated or changed after that
inventory was retained. **They do not establish the inventory's authority or its
completeness against the owning source.** An exporter can shorten both the graph and
the inventory and produce a structurally consistent wrong source minimum. The test
`test_self_issued_shortened_inventory_is_explicitly_outside_real_proof_boundary`
executes that counterexample. Nonempty evidence strings also cannot establish this
authority. No generic `complete=true` input or production minimum flag is exposed.
Partial-cut certificates are unsupported: a cut cannot stand in for the retained
full inventory. The result is deliberately not connected to route normalization.

## Pinned Valhalla 3.5.1 findings

These findings come from the upstream pinned sources independently read in this
work, not from synthetic route assertions:

| Upstream source | Relevant behavior | Retained file SHA-256 |
| --- | --- | --- |
| [`src/sif/pedestriancost.cc`](https://github.com/valhalla/valhalla/blob/3.5.1/src/sif/pedestriancost.cc) | Lines 696–740: `EdgeCost` returns separate cost and seconds; shortest mode uses distance for cost; alley, driveway, roundabout, grade and other factors can change generalized cost. Lines 537–568 and 744–767 distinguish elapsed transition time from penalties. | `f45611ece3cf42d9dc394ab185bf720f0405415cba521310f087b69f1ffd7cbd` |
| [`src/thor/dijkstras.cc`](https://github.com/valhalla/valhalla/blob/3.5.1/src/thor/dijkstras.cc) | Lines 208–233 compare and update labels using `newcost.cost`, not elapsed `secs`. Lines 810–820 seed partial-edge traversal and add correlation distance to cost only. | `bf433da492e9614ab0e06c2658c8a252701267c54a0cb51949e9da518f7eeaca` |
| [`src/tyr/expansion_serializer.cc`](https://github.com/valhalla/valhalla/blob/3.5.1/src/tyr/expansion_serializer.cc) | Lines 56–63 cast duration and cost to `uint64_t`; HTTP expansion loses subsecond values. | `937663b7d1829db6cd1dd5f72a398249769285c47a10cbdab0bc829518a5e441` |

Pedestrian `Allowed` at lines 637–664 also depends on predecessor dead-end state,
user avoid edges, edge restrictions, time and timezone. Its intended max-distance
check has an important literal detail at line 651:
`(!allow_transit_connections_ && pred.path_distance() + edge->length()) > max_distance_`.
The parenthesized expression is a boolean, so at `max_distance >= 1` this specific
check does not reject a path for distance. Comments alone would suggest different
semantics. `AllowedReverse` at lines 669–691 explicitly omits max walking distance.
Complex restrictions are checked by the search as well. An incoming-edge ID alone
is therefore not automatically a sufficient state; every claimed resource constraint
must also be checked against executed source behavior. Time-dependent rules cannot
be treated as static without a separately justified policy or adequate finite state
expansion. This requires source-level review of the full profile and algorithms;
this task did not certify a complete Valhalla state exporter or change upstream code.

`shortest=true`, HTTP 200, a plausible route time, all geometric edge lines, or an
exhausted generalized-cost expansion do not prove the required elapsed minimum.
The existing six-decimal endpoint serialization interval also cannot prove an exact
physical access match. Current runtime attestations remain external trust inputs;
this reference module does not make them independently verified.

## Minimal real proof paths

The straightforward path needs an independently audited source inventory and a
read-only pinned exporter of the actual admissible state graph. It must retain
graph/build/profile/option identities, all physical connector alternatives, exact
correlation state identities and split-edge rules, allowed directed transitions,
separate edge/transition elapsed weights, all relevant history/resources, and
complete state and arc rosters or a stronger independently checked equivalent.
Running the exact oracle on that export and checking a feasible candidate's total
can establish a scoped minimum **conditional on those independently established
source semantics and completeness obligations**. This module alone cannot discharge
them. A large exact-Fraction state expansion also needs a measured resource budget;
the synthetic performance here is not evidence of real-graph scalability.

A smaller valid certificate can use a **relaxation**. Construct a source-bound complete
supergraph of every admissible physical route with transition weights no greater than
the true elapsed weights. For example, dropping nonnegative turn delays/restrictions
can give a lower bound, provided all real directed paths and connector alternatives
are still represented. Independently validate one feasible real route and its exact
physical total `U`. If the exact relaxation minimum `L` equals `U`, then every feasible
route is at least `L`, while the candidate attains `U`; its elapsed minimum is proven.
If `L < U`, the result is only a bound and proves no minimum. A shortened export is
not a supergraph, and an omitted entrance invalidates that argument. The relaxation
certificate's source-to-supergraph inclusion check is not implemented here.

Another possible certificate is a verified closed search ball/cut: all initial states
and outgoing transitions below the incumbent total must be exhaustively enumerated,
and every unexpanded boundary exit needs a justified lower bound at least that total.
The bound must include the initial physical connector and any admissible terminal
alternative. Nonnegative weights alone do not certify a missing outgoing transition
from an interior state. This oracle intentionally requires the full inventory instead.

An available category index then needs a separate authoritative destination catalog
for the product's category and serving scope. A school municipal roster, partial
physical-campus list, or one selected destination is not a complete catalog for all
public/state schools. A territory score additionally needs every positive-population
point's selected components and complete population coverage. These independent
obligations remain even if one physical route minimum is established. Unknown values
must remain unknown; the formulas and H are unchanged.

## Executed evidence

The new synthetic oracle tests include wrong generalized-cost objective, omitted or
double-counted connectors, transition-time omission, altered precision, missing
states/arcs/endpoints, rounded correlation, truncated cut, content change, direction,
turn-history states, zero/source-equals-sink/cycle/tie cases and multi-entrance/exit
alternatives. Exact Dijkstra is compared against independent enumeration of every
simple path in 75 deterministic six-state graphs; nonnegative weights justify ignoring
cycles for that independent minimum check. Input permutations must preserve the result.

The walk integration regressions retain real geography but use clearly synthetic
routing attestations. They also verify that one missing route at a positive-population
point blocks the territory score rather than renormalizing onto known points, and
that successful JSON with missing actual endpoint evidence remains unknown. Existing
six-decimal tests verify that best-known time survives while minimum/index stay unknown.
No real physical total-trip minimum or real available accessibility index was independently executed.

## Executed source-bound relaxation

`native/source_bound_walk.cc`, `urban/walk/source_bound.py` and the executor
`examples/audit_source_bound_walk.py` now implement the separate source-bound
relaxation path described above. The native helper links the pinned 3.5.1 runtime
library, reads every source tile at all hierarchy levels, inventories directed
edges and hierarchy transitions, and obtains unrounded native correlations.
It replays the candidate with the actual access/restriction/transition functions.
See `native/README.md` for the exact additive binary32 objective and source
inclusion argument; it does not claim equality with HTTP clock serialization.

The real Kaliningrad pilot inventory contained 121 tiles, 132178 nodes, 300288
directed edges and six native endpoint alternatives. Its zero-turn relaxed lower
bound was exactly `2082035309/4194304` seconds (about 496.395900011), while native
replay of that candidate took `2245613165/4194304` seconds (about 535.395900011).
The 39-second gap includes actual elapsed transition costs and proves no minimum.
The subsequent complete minimum-turn relaxation changed the candidate and tightened
the network interval to exactly `2157902905/4194304` through `2183068729/4194304`
seconds (about 514.484144449 through 520.484144449), a six-second gap. The lower
bound minimizes actual elapsed turn costs over every inventoried immediate
predecessor, including hierarchy aliases; it does not prune restrictions or history.
These are bounds in the declared native endpoint domain. Physical lower and upper
bounds remain null because physical entrance completeness and access-to-native
projection offsets are unconfirmed. The category index remains null.

The executor records immutable tile/source/library/binary/request/export hashes
in an external receipt; changing a source tile or its roster aborts execution.
Config/profile hashes and actual directory-only source binding are now checked
before execution; unexpected extracts, remote/compressed/symlink sources fail.
Audit outputs use exclusive per-run directories with copied/pinned executable and
source inputs. The earlier prototype's shared output files were overwritten during
iteration and must not be presented as immutable receipts. Physical lower bounds
and minimum flags remain unavailable even if a complete roster is asserted: scalar
access weights cannot be added to a minimum over alternatives with different costs.
Raw pilot artifacts live under
`/Users/kirito/waydex-phase2-runtime/source-bound-walk/`, outside Git. The existing
HTTP adapter still preserves its observed/best-known result independently and
does not accept this relaxation receipt as a production minimum attestation.

## Executed closed-ball immediate-turn certificate

`urban/walk/turn_certificate.py` implements the bounded next step. A currently
replayed feasible incumbent `U` is bound to the same export, request, config,
profile, library and binary hashes. Exact zero-turn distances start from **every**
origin fragment, and include directed hierarchy transitions. The closed ball uses
`d(v) <= U`, with no floating tolerance. Every admissible improving route has each
full-edge endpoint in that ball, because nonnegative turn times imply
`d(v) <= actual prefix <= U`.

States are the last positive-fraction traversed edges; zero seconds do not erase
an incoming edge. Every actual immediate outgoing pair is retained at the same
node or after one directed hierarchy hop, matching the pinned expansion domain.
The native helper evaluates every requested turn using `TransitionCost.secs`;
the checker requires exact pair IDs, source scope and plan hashes. It includes
terminal fragments even if their full edge ends outside the ball, handles direct
same-edge source-to-terminal paths separately, and gives zero source fragments
no invented predecessor. Full history, access and distance constraints remain
relaxed until the final native feasibility replay.

The real pilot used 794 closed-ball nodes, 1927 incoming-edge states and 5640
turn pairs. Its relaxed optimum and independent native feasible replay both
equalled exactly `1088850011/2097152` seconds (about 519.2041449546814). This proves
the **scoped network minimum for the exact additive binary32-weight model**.
It does not prove the physical total-trip minimum or an accessibility index.
Physical lower/upper bounds remain null and the physical minimum flag remains false.

A separate native actor trace confirmed the same 22 positive-fraction directed
edge IDs as the certified optimum. The actor's accumulated clock was
519.2041625976562 seconds, `37/2097152` seconds above the exact additive sum, and
its JSON summary was 519.204 seconds. The guarantee deliberately distinguishes
these three numerical representations. It does not replace the product's
observed 547.404-second physical best-known result with a proven category score.

Run the exclusive executor with `--exact-turns` to reproduce the complete
source-bound export, incumbent, closed-ball pair roster, exact search and final
replay. Each run copies both checker modules and its native source/binary/compiler
manifest, and retains every request/response and digest under a fresh run ID.
