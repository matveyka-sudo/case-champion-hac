# Private Novorossiysk walking graph

`compose.product-novorossiysk-walk.yaml` adds the optional
`valhalla-novorossiysk:8002` endpoint to the existing product network. Apply it
after `compose.product.yaml` and `compose.product-urban-walk.yaml`, retaining the
assistant and independently reviewed web overlays when those are in use. It
uses the existing API image and leaves the product PostgreSQL and observation
volumes unchanged. It adds no host port or Kaliningrad router.

Set these variables in the private operator environment file:

```dotenv
WAYDEX_NOVOROSSIYSK_GRAPH_DIR=/absolute/path/to/verified/novorossiysk/graph
WAYDEX_NOVOROSSIYSK_GRAPH_BINDING_DIR=/absolute/path/to/immutable/source-binding-packet
WAYDEX_NOVOROSSIYSK_GRAPH_BINDING_MANIFEST=binding.json
WAYDEX_NOVOROSSIYSK_GRAPH_BINDING_SHA256=RECORDED_BINDING_SHA256
WAYDEX_NOVOROSSIYSK_PARENT_ARTIFACT=parent-artifact.json
WAYDEX_NOVOROSSIYSK_PARENT_SHA256=RECORDED_PARENT_ARTIFACT_SHA256
```

The binding packet contains the canonical snapshot, original source manifest,
graph manifest and parent build identity. Every relative FileRef must remain
inside that packet. The checker reads the separately mounted physical graph and
validates its original source or explicit derivation, image, configuration,
walking profile, binary version and complete tile inventory against the packet.
Both mounts are read-only and missing host directories fail closed.

Startup order is `graph-check-novorossiysk` → `valhalla-novorossiysk` →
`runtime-check` → migrations → API and the existing single worker. The physical
graph check makes no routing or paid-provider requests. The router readiness
check uses its local `/status` endpoint. Product runtime validation must also
admit the intended hash-pinned Novorossiysk profile; a successful graph check
does not establish a route, minimum or index.

The source checker is bounded to 512 MiB RAM, 512 MiB RAM plus swap, one CPU and
64 processes. The router is bounded to 1 GiB RAM, 1 GiB RAM plus swap, two CPUs
and 128 processes; its temporary filesystem is capped at 64 MiB. Both have
rotating 10 MiB × three logs. Account for these additional limits before
activation alongside other running stacks.

Run `docker compose ... config --quiet` with the complete overlay list and the
same private environment file before activation. Expanded Compose output
contains credentials and must stay private. Build from the reviewed immutable
source release, record the image ID, then verify candidate manifest and
executor fingerprints agree before changing the API or worker. Starting this
overlay and activating a broader profile remain separate operator actions.

Preserve historical probe artifacts and their original endpoints. Reusing a
physical graph supplies no authority to retag an old observation for a newer
canonical snapshot. Keep an existing temporary router and all datasets and
volumes until the operator coordinates the transition.
