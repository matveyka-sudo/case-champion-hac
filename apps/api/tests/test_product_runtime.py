"""TEST_ONLY: immutable input loading, semantic tamper guards and dynamic requests."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a2_api.adapters import product
from a2_api.adapters.product_inputs import InputDocument, UrbanInputs
from a2_api.adapters.product_runtime import (
    FileRef,
    Profile,
    input_fingerprints,
    load_runtime,
    validate_inputs,
)
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob, V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_engine import UrbanCoverage, UrbanSearchScope, prepare_walk_inputs

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def transport_facts():
    snapshot = AcceptedData.model_validate(
        json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())[
            "payload"
        ]
    )
    config = ProductRegistry()
    raw = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id="city-1",
        departure_at=datetime(2026, 11, 9, tzinfo=UTC),
        methodology_version=config.methodology.version,
        config_hash=config.config_hash,
        search_parameters_id="TEST_ONLY-search",
    )
    category = raw.destinations[0].categories
    facts = UrbanInputs(
        kind="urban_passenger",
        context=raw.context,
        points=raw.points,
        destinations=raw.destinations,
        candidates=(),
        area=raw.area,
        population_coverage=raw.population_coverage,
        coverage=tuple(
            UrbanCoverage(
                UrbanSearchScope(raw.context, p.point_id, c, "walk", 14400),
                False,
                False,
                p.source_refs,
                frozenset(d.destination_id for d in raw.destinations),
            )
            for p in raw.points
            for c in category
        ),
    )
    profile = Profile(
        profile_id="TEST_ONLY-walk",
        analysis_kind="urban_passenger",
        city_id="city-1",
        passenger_modes=["walk"],
        categories=sorted(category),
        valid_from="2026-11-01T00:00:00Z",
        valid_to="2026-12-01T00:00:00Z",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        graph_fingerprint="0" * 64,
        routing_profile_fingerprint="0" * 64,
        max_route_pairs=100,
    )
    graph, costing = input_fingerprints(profile, facts, config)
    profile = profile.model_copy(
        update={"graph_fingerprint": graph, "routing_profile_fingerprint": costing}
    )
    return snapshot, facts, profile


def write_document(path, payload):
    content = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(content)
    return {"path": path.name, "sha256": hashlib.sha256(content).hexdigest()}


def configure(tmp_path, monkeypatch, transport_facts):
    snapshot, facts, profile = transport_facts
    reference = write_document(
        tmp_path / "TEST_ONLY-snapshot.json", snapshot.model_dump(mode="json")
    )
    reference.update(snapshot_id=snapshot.snapshot_id, content_hash=profile.snapshot_hash)
    supplement = write_document(
        tmp_path / "TEST_ONLY-inputs.json",
        InputDocument(schema_version="waydex-engine-inputs/1", inputs=facts).model_dump(
            mode="json"
        ),
    )
    manifest = dict(
        schema_version="waydex-product-runtime/1",
        snapshots=[reference],
        territories=[
            dict(territory_id="city-1", name="Калининград", level="city", timezone="Europe/Moscow")
        ],
        profiles=[
            profile.model_copy(update={"inputs": FileRef.model_validate(supplement)}).model_dump(
                mode="json"
            )
        ],
    )
    manifest_reference = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_PATH", str(tmp_path / manifest_reference["path"]))
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", manifest_reference["sha256"])
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + manifest_reference["sha256"])
    return manifest


def request_for(snapshot, **changes):
    values = dict(
        schema_version="2",
        analysis_kind="urban_passenger",
        city_id="city-1",
        passenger_mode="walk",
        categories=["adult_polyclinic"],
        priorities={"adult_polyclinic": "medium"},
        departure_at="2026-11-09T00:00:00Z",
        traffic_mode="static",
        snapshot_id=snapshot.snapshot_id,
        methodology=ProductRegistry().methodology,
    )
    values.update(changes)
    return UrbanPassengerRequest.model_validate(values)


def claim_for(snapshot, request):
    config = ProductRegistry()
    from a2_api.core.config import RuntimeSettings

    context = V2JobContext(
        schema_version="2",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        versions=VersionRefs(
            snapshot_id=snapshot.snapshot_id,
            graph_version=snapshot.provenance.graph_version,
            calendar_version=snapshot.provenance.calendar_version,
            population_version=snapshot.provenance.population_version,
            methodology_version=config.methodology.version,
            config_hash=config.config_hash,
            code_version=RuntimeSettings().code_version,
        ),
        analysis_id="TEST_ONLY-analysis",
        normalized_request=request,
        access_scope="TEST_ONLY-scope",
    )
    job = Job(
        job_id="TEST_ONLY-job",
        status="running",
        created_at=request.departure_at,
        started_at=request.departure_at,
        finished_at=None,
        run_id=None,
        error=None,
        trace_id="TEST_ONLY-trace",
        bundle_id=snapshot.snapshot_id,
        input_hash="TEST_ONLY-inputs",
        code_version=context.versions.code_version,
    )
    return ClaimedJob(
        job=job,
        kind="analysis",
        request=request,
        context=context,
        cache_key="TEST_ONLY-cache",
        claim_token="TEST_ONLY-claim",
    )


def test_hashed_runtime_uses_dynamic_departure_and_priorities(
    tmp_path, monkeypatch, transport_facts
):
    configure(tmp_path, monkeypatch, transport_facts)
    runtime = load_runtime()
    snapshot = transport_facts[0]
    first = request_for(snapshot)
    second = request_for(
        snapshot,
        departure_at=first.departure_at + timedelta(minutes=15),
        priorities={"adult_polyclinic": "high"},
    )
    assert runtime.select(first, snapshot)[0] == runtime.select(second, snapshot)[0]
    for request in (first, second):
        result = product.calculate(claim_for(snapshot, request))
        assert result.request == request
        assert result.status == "partial"
        assert result.metrics[0].value == 1200
        assert all(score.value is None for score in result.scores)
        assert all(not score.ranking_eligible for score in result.scores)


@pytest.mark.parametrize("mutation", ["population", "access", "category", "fingerprint"])
def test_same_identity_cannot_change_accepted_facts(transport_facts, mutation):
    snapshot, facts, profile = transport_facts
    if mutation == "population":
        facts = facts.model_copy(update={"points": (replace(facts.points[0], population=999),)})
    elif mutation == "access":
        facts = facts.model_copy(
            update={"points": (replace(facts.points[0], access_node_id="foreign-node"),)}
        )
    elif mutation == "category":
        facts = facts.model_copy(
            update={
                "destinations": (replace(facts.destinations[0], categories=frozenset({"school"})),)
            }
        )
    if mutation != "fingerprint":
        graph, costing = input_fingerprints(profile, facts, ProductRegistry())
        profile = profile.model_copy(
            update={"graph_fingerprint": graph, "routing_profile_fingerprint": costing}
        )
    else:
        profile = profile.model_copy(update={"graph_fingerprint": "f" * 64})
    with pytest.raises(ValueError, match="SEMANTICS|FINGERPRINT"):
        validate_inputs(profile, facts, snapshot, ProductRegistry())


def test_changed_input_bytes_disable_only_affected_kind(tmp_path, monkeypatch, transport_facts):
    configure(tmp_path, monkeypatch, transport_facts)
    (tmp_path / "TEST_ONLY-inputs.json").write_text("{}")
    assert load_runtime().profiles == {}
    capabilities = product.registry().capabilities()
    assert not any(kind.calculation_available for kind in capabilities.analyses)
    assert all(kind.reason_code == "RUNTIME_INPUTS_UNAVAILABLE" for kind in capabilities.analyses)


def test_nonfinite_duplicate_keys_and_cumulative_budget_are_rejected(tmp_path):
    from a2_api.adapters.product_runtime import read_document

    for content in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        path = tmp_path / "TEST_ONLY.json"
        path.write_bytes(content)
        with pytest.raises(ValueError):
            read_document(path, hashlib.sha256(content).hexdigest())
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="CUMULATIVE"):
        read_document(path, hashlib.sha256(b"{}").hexdigest(), [1])


def test_profile_scope_and_route_budget_refuse_unsupported_requests(
    tmp_path, monkeypatch, transport_facts
):
    configure(tmp_path, monkeypatch, transport_facts)
    runtime = load_runtime()
    snapshot = transport_facts[0]
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_UNAVAILABLE"):
        runtime.select(request_for(snapshot, passenger_mode="public_transport"), snapshot)
    with pytest.raises(DomainError, match="RUNTIME_INPUTS_UNAVAILABLE"):
        runtime.select(request_for(snapshot, departure_at="2027-01-01T00:00:00Z"), snapshot)
