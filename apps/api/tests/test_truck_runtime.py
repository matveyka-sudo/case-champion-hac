"""Synthetic provider controls; real route replay is a separate acceptance gate."""

import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from a2_api.adapters import product, truck_runtime
from a2_api.adapters.product_runtime import Manifest, Runtime, input_fingerprints, validate_inputs
from a2_api.adapters.truck_graph import verify_graph
from a2_api.core.truck_routing import TruckRoutingBinding
from a2_api.providers import road_observations as road
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import TerritoryOption
from a2_contracts.v2.requests import UrbanCargoRequest
from a2_contracts.v2.results import AnalysisResult
from a2_engine import CargoCompatibility, CargoNode, TruckRoute, TruckSearchReceipt
from test_product_runtime import claim_for
from test_product_runtime import transport_facts as transport_facts
from test_product_source_guards import cargo_inputs


def response_body(seconds=120):
    return {
        "trip": {
            "status": 0,
            "units": "kilometers",
            "summary": {"time": seconds},
            "legs": [
                {
                    "summary": {"time": seconds},
                    "shape": "TEST_ONLY-shape",
                    "maneuvers": [{"travel_mode": "drive"}],
                }
            ],
        }
    }


@pytest.fixture
def binding(tmp_path, monkeypatch):
    graph = tmp_path / "graph"
    (graph / "tiles").mkdir(parents=True)
    tile = graph / "tiles/1.gph"
    tile.write_bytes(b"TEST_ONLY-tile")
    config = graph / "valhalla.json"
    config.write_text('{"mjolnir":{"tile_dir":"/data/tiles","tile_extract":"/data/tiles.tar"}}')
    manifest = {
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "files": [
            {
                "path": "tiles/1.gph",
                "bytes": tile.stat().st_size,
                "sha256": hashlib.sha256(tile.read_bytes()).hexdigest(),
            }
        ],
    }
    source = graph / "valhalla-build-manifest.json"
    source.write_text(json.dumps(manifest))
    os.utime(graph / "tiles", (100, 100))
    monkeypatch.setenv("A2_TRUCK_TEST_GRAPH_DIR", str(graph))
    monkeypatch.setenv("A2_WALK_OBSERVATION_DIR", str(tmp_path))
    return TruckRoutingBinding(
        schema_version="waydex-truck-routing/1",
        endpoint_url="http://test-router:8002",
        graph_directory_env="A2_TRUCK_TEST_GRAPH_DIR",
        graph_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        engine_version="3.5.1",
        tileset_last_modified=100,
        max_pairs=100,
        max_seconds=30,
        requests_per_second=20,
    )


def transport(calls, body=None, wrong_graph=False):
    def handle(request):
        calls.append(request)
        if request.url.path == "/status":
            return httpx.Response(
                200, json={"version": "3.5.1", "tileset_last_modified": 101 if wrong_graph else 100}
            )
        return httpx.Response(200, json=response_body() if body is None else body)

    return httpx.MockTransport(handle)


def observe(binding, client, context=None):
    deadline = time.monotonic() + 5
    with road.observation_store(deadline) as store:
        return road.observe(
            client,
            binding,
            store,
            context or {"TEST_ONLY": "scope"},
            {"costing": "truck"},
            deadline,
        )


def test_positive_cache_and_scoped_identity(binding):
    calls = []
    with httpx.Client(transport=transport(calls)) as client:
        first = observe(binding, client)
        assert road.network_seconds(first) == 120
        assert observe(binding, client) == first
        assert len(calls) == 3  # status / route / status; no second HTTP request
        changed = observe(binding, client, {"TEST_ONLY": "another-session"})
        assert changed.identity != first.identity
        assert len(calls) == 6


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"trip": []},
        {"trip": {"summary": []}},
        response_body(True),
        response_body(-1),
        response_body(float("inf")),
        response_body(10**400),
        {"trip": {"status": 0, "units": "kilometers", "summary": {"time": 120}, "legs": [None]}},
    ],
)
def test_malformed_nested_responses_remain_unknown_and_are_not_cached(binding, body):
    calls = []

    # Encode manually because the HTTPX JSON helper correctly rejects nonfinite values.
    def handle(request):
        if request.url.path == "/status":
            return httpx.Response(200, json={"version": "3.5.1", "tileset_last_modified": 100})
        calls.append(request)
        return httpx.Response(200, content=json.dumps(body))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert road.network_seconds(observe(binding, client)) is None
        assert road.network_seconds(observe(binding, client)) is None
    assert len(calls) == 2


def test_wrong_router_graph_rejects_before_route_or_cache(binding):
    calls = []
    with httpx.Client(transport=transport(calls, wrong_graph=True)) as client:
        with pytest.raises(ValueError, match="BUILD_MISMATCH"):
            observe(binding, client)
    assert len(calls) == 1 and calls[0].url.path == "/status"


def test_unavailable_provider_is_unknown(binding):
    def unavailable(request):
        raise httpx.ConnectError("TEST_ONLY unavailable", request=request)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        observation = observe(binding, client)
    assert road.network_seconds(observation) is None
    assert observation.http_status == 502


@pytest.mark.parametrize("mutation", ["tile", "extra", "config", "mtime", "symlink", "archive"])
def test_actual_graph_inventory_is_checked(binding, mutation, monkeypatch):
    from pathlib import Path

    root = Path(os.environ[binding.graph_directory_env])
    verify_graph(binding)
    if mutation == "tile":
        (root / "tiles/1.gph").write_bytes(b"TEST_ONLY-evil")
    elif mutation == "extra":
        (root / "tiles/extra.gph").write_bytes(b"TEST_ONLY-extra")
    elif mutation == "config":
        (root / "valhalla.json").write_text("{}")
    elif mutation == "mtime":
        os.utime(root / "tiles", (101, 101))
    elif mutation == "archive":
        (root / "tiles.tar").write_bytes(b"TEST_ONLY-unverified-preferred-archive")
    else:
        (root / "tiles/extra.gph").symlink_to(root / "tiles/1.gph")
    with pytest.raises(ValueError, match="TRUCK_GRAPH"):
        verify_graph(binding)


def test_corrupt_cached_raw_response_rejected(binding, tmp_path):
    calls = []
    with httpx.Client(transport=transport(calls)) as client:
        first = observe(binding, client)
        path = tmp_path / "road-observations-v1" / (first.identity + ".json")
        document = json.loads(path.read_text())
        document["response_raw"] = json.dumps(response_body(1))
        path.write_text(json.dumps(document))
        with pytest.raises(ValueError, match="BYTES_CHANGED"):
            observe(binding, client)
    assert len(calls) == 3


def test_budget_and_deadline_fail_before_unbounded_work(binding, monkeypatch):
    monkeypatch.setattr(road, "MAX_STORE_BYTES", 1)
    with httpx.Client(transport=transport([])) as client:
        with pytest.raises(ValueError, match="STORAGE_BUDGET"):
            observe(binding, client)
        with pytest.raises(TimeoutError, match="DEADLINE"):
            road.observe(client, binding, None, {}, {}, time.monotonic() - 1)


def configure_worker(binding, transport_facts, monkeypatch):
    snapshot, inputs, profile, registry = cargo_inputs(transport_facts)
    port = snapshot.freight_nodes[0].model_copy(update={"kind": "port"})
    snapshot = snapshot.model_copy(update={"freight_nodes": [port]})
    context = replace(inputs.coverage.context, build_id=binding.graph_manifest_sha256)
    inputs = inputs.model_copy(
        update={
            "coverage": replace(inputs.coverage, context=context),
            "sites": tuple(replace(s, context=context) for s in inputs.sites),
            "nodes": (CargoNode(port.freight_node_id, "port", "port", context, ("e-1",)),),
        }
    )
    profile = profile.model_copy(
        update={
            "truck_routing": binding,
            "static_observations": True,
            "snapshot_hash": canonical_hash(snapshot.model_dump(mode="json")),
        }
    )
    graph, costing = input_fingerprints(profile, inputs, registry)
    profile = profile.model_copy(
        update={"graph_fingerprint": graph, "routing_profile_fingerprint": costing}
    )
    validate_inputs(profile, inputs, snapshot, registry)
    runtime = Runtime(
        "d" * 64,
        Manifest(
            schema_version="waydex-product-runtime/1",
            snapshots=[],
            territories=[],
            profiles=[profile],
        ),
        {snapshot.snapshot_id: snapshot},
        {profile.profile_id: (profile, inputs)},
        frozenset(),
    )
    monkeypatch.setattr(product, "load_runtime", lambda **kwargs: runtime)
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + runtime.digest)
    request = UrbanCargoRequest(
        schema_version="2",
        analysis_kind="urban_cargo",
        city_id="city-1",
        departure_at=profile.valid_from,
        traffic_mode="static",
        snapshot_id=snapshot.snapshot_id,
        cargo_profile_id=inputs.profile.profile_id,
        direction="outbound",
        methodology=registry.methodology,
    )
    return runtime, snapshot, inputs, profile, claim_for(snapshot, request)


def test_dispatcher_computes_missing_pairs_then_reuses_cache_without_inventing_index(
    binding, transport_facts, monkeypatch
):
    runtime, snapshot, inputs, profile, claim = configure_worker(
        binding, transport_facts, monkeypatch
    )
    calls = []
    client_type = httpx.Client
    monkeypatch.setattr(
        truck_runtime.httpx,
        "Client",
        lambda **kwargs: client_type(transport=transport(calls), **kwargs),
    )
    result = product.calculate(claim)
    observed = [m for m in result.metrics if m.name == "observed_truck_network_seconds"]
    assert observed and all(m.value == 120 and m.completeness == "partial" for m in observed)
    assert all(s.value is None and not s.ranking_eligible for s in result.scores)
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result
    count = len(calls)
    second = product.calculate(claim)
    assert len(calls) == count and second == result
    assert inputs.routes == ()  # never mutate accepted inputs
    route_calls = [r for r in calls if r.url.path == "/route"]
    assert len(route_calls) == len(inputs.sites) * len(inputs.nodes)
    assert json.loads(route_calls[0].content)["locations"][0] == {
        "lat": snapshot.freight_sites[0].location.lat,
        "lon": snapshot.freight_sites[0].location.lon,
    }


def test_bounded_prepared_pairs_produce_scores_at_requested_time(
    binding, transport_facts, monkeypatch
):
    runtime, snapshot, inputs, profile, claim = configure_worker(
        binding, transport_facts, monkeypatch
    )
    _, _, _, registry = cargo_inputs(transport_facts)
    request = claim.context.normalized_request
    assert isinstance(request, UrbanCargoRequest)
    runtime.manifest.territories.append(
        TerritoryOption(territory_id=request.city_id, level="city", name="Test", timezone="UTC")
    )
    request = request.model_copy(update={"departure_at": request.departure_at + timedelta(hours=8)})
    claim = claim_for(snapshot, request)
    prepared_at = profile.valid_from
    node = inputs.nodes[0]
    routes, receipts = [], []
    for index, site in enumerate(inputs.sites, start=1):
        compatibility = CargoCompatibility(
            f"compat-{index}",
            site.context,
            inputs.profile.profile_id,
            inputs.profile.version,
            inputs.profile.requirements_hash,
            site.site_id,
            node.node_id,
            "static-truck",
            request.direction,
            profile.valid_from,
            profile.valid_to,
            "confirmed",
            ("e-1",),
        )
        route = TruckRoute(
            f"truck-{index}",
            site.context,
            inputs.profile.requirements_hash,
            inputs.profile.vehicle,
            site.site_id,
            node.node_id,
            "static-truck",
            request.direction,
            prepared_at,
            600 * index,
            30,
            30,
            "reachable",
            compatibility,
            ("e-1",),
        )
        routes.append(route)
        receipts.append(
            TruckSearchReceipt(
                route.route_id,
                route.fingerprint,
                truck_runtime.route_scope_id(route),
                True,
                True,
                ("e-1",),
            )
        )

    def calculate(routes, receipts):
        prepared = inputs.model_copy(update={"routes": tuple(routes), "receipts": tuple(receipts)})
        bounded = profile.model_copy(update={"compact": True, "truck_routing": None})
        graph, costing = input_fingerprints(bounded, prepared, registry)
        bounded = bounded.model_copy(
            update={
                "graph_fingerprint": graph,
                "routing_profile_fingerprint": costing,
            }
        )
        validate_inputs(bounded, prepared, snapshot, registry)
        runtime.profiles[bounded.profile_id] = (bounded, prepared)
        return product.calculate(claim)

    result = calculate(routes, receipts)
    h = next(m.value for m in result.metrics if m.name == "urban_h_seconds")
    assert h is not None
    expected = sum(100 * 2 ** (-r.seconds / h) for r in routes) / len(routes)
    aggregate = next(s for s in result.scores if s.name == "cargo_access_index")
    assert aggregate.value == pytest.approx(expected)
    assert aggregate.completeness == "partial"
    assert all(not s.ranking_eligible for s in result.scores)
    assert result.status == "partial"
    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result

    with pytest.raises(ValueError, match="TRUCK_SEARCH_RECEIPT_MISMATCH"):
        calculate(routes, [replace(receipts[0], route_fingerprint="0" * 64), *receipts[1:]])

    route = replace(routes[0], origin_access_seconds=None)
    changed = replace(receipts[0], route_fingerprint=route.fingerprint)
    incomplete = calculate([route, *routes[1:]], [changed, *receipts[1:]])
    assert next(s for s in incomplete.scores if s.name == "cargo_access_index").value is None
    assert "TRUCK_ACCESS_UNKNOWN" in incomplete.limitations
