# Partial freight observations

The urban cargo dispatcher accepts registered physical inventories and explicit
truck-costing road observations. A missing origin/destination approach is `null`;
it cannot become a zero, full travel time, confirmed absence or proven minimum.
`observed_truck_network_seconds` is a separate movement metric, not delivery time
or an index input. Unknown compatibility remains visible. No mathematical formula
changes are made.

Only operator-approved static observations without minimum receipts may be used
at a new departure inside the profile validity. Compatibility is evaluated again
for the requested time. Search receipts remain bound to their original query.
An inventory with no accepted infrastructure nodes returns unknown category
scores and its actual physical locations instead of raising an empty-type error.

`SnapshotAvailability.analysis_kinds` is an optional additive field. Production
runtime fills it from successfully validated profiles; the client filters source
selection by analysis kind. This prevents passenger data from accidentally being
selected for a cargo request. A missing field preserves old-server behavior; an
empty list explicitly means no executable analysis profile for that snapshot.

## Local observation release, 2026-11-11

The candidate preserves 2,222 Moscow sites (zero accepted freight nodes), 250
Kaliningrad sites and three ports, and 86 Novorossiysk sites and two ports. Four
other accepted Novorossiysk pipeline terminals are retained as inventory but are
not reclassified as ports. Every site/port pair in both directions was queried:
1,500 Kaliningrad and 344 Novorossiysk observations. The bounded local collection
took 21.255 seconds. These are real local Valhalla responses, not synthetic tests.

The declared routing model is a 20-tonne truck, 4 m high, 2.5 m wide and 12 m long,
using explicit free-flow speeds. It is a model configuration, not evidence that
an actual carrier/vehicle can access a particular facility. Full physical
approaches, cargo compatibility and minimum proof are unknown. Consequently
these observations do not establish freight accessibility or delivery indices.
Food/nonfood container profiles and ordinary/urgent selections retain the same
physical facts with explicit unknown handling/compatibility.

The input release contains request/response hashes, raw responses, before/after
router version checks, source snapshot hashes and graph-build manifest hashes.
Large artifacts are external to Git. The existing immutable runtime archive
format packages these files. Valhalla tiles are a **separate required artifact**;
the input archive does not contain every deployed router's tile directory.
Restoring ML2's original ZIP alone does not restore these derived observations.

Use `compose.product-cargo.yaml` last in the existing Compose overlay list. It
explicitly sets the cumulative input validation budget to 160 MiB. The default
remains 96 MiB, each document remains limited to 32 MiB, and the configured API
and worker memory limits remain unchanged. The 33-profile candidate exceeded
128 MiB of validation reads; a diagnostic registration at 160 MiB loaded all 33
profiles without failures, taking 10.958 s and peaking at 938,380 KiB RSS in the
Linux container. This measures registration, not worst-case job memory.

Known integration limits: these initial truck routes and intercity car routes
are prepared observations. Jobs run ML1 over them; they do not yet fetch missing
road pairs on demand. Automatic collection, shared public result publication,
full delivery-operation chains and a clean-server replay of this exact release
are not claimed complete.
