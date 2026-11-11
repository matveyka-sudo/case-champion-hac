"""TEST_ONLY: immutable input loading, semantic tamper guards and dynamic requests."""

import hashlib
import json
import os
import subprocess
import sys
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
    with pytest.raises(DomainError, match="REQUEST_OUTSIDE_SUPPORTED_PRESETS"):
        runtime.select(request_for(snapshot, passenger_mode="public_transport"), snapshot)
    with pytest.raises(DomainError, match="REQUEST_OUTSIDE_SUPPORTED_PRESETS"):
        runtime.select(request_for(snapshot, departure_at="2027-01-01T00:00:00Z"), snapshot)
    with pytest.raises(DomainError, match="REQUEST_OUTSIDE_SUPPORTED_PRESETS"):
        runtime.select(request_for(snapshot, area_id="TEST_ONLY-known-district"), snapshot)


def real_partial_school_inputs():
    """Read accepted real facts; no external routing or provider calls."""
    from a2_data_pipeline.contract_v2 import load_accepted_data

    snapshot = load_accepted_data("kaliningrad-urban-2026-11-10.3")
    config = ProductRegistry()
    raw = prepare_walk_inputs(
        snapshot.model_dump(mode="json"),
        city_id="kaliningrad",
        departure_at=datetime(2026, 11, 10, tzinfo=UTC),
        methodology_version=config.methodology.version,
        config_hash=config.config_hash,
        search_parameters_id="TEST_ONLY-readiness",
    )
    coverage = tuple(
        UrbanCoverage(
            UrbanSearchScope(raw.context, p.point_id, "school", "walk", 14400),
            False,
            False,
            p.source_refs,
            frozenset(d.destination_id for d in raw.destinations if "school" in d.categories),
        )
        for p in raw.points
    )
    facts = UrbanInputs(
        kind="urban_passenger",
        context=raw.context,
        points=raw.points,
        destinations=raw.destinations,
        coverage=coverage,
        area=raw.area,
        population_coverage=replace(raw.population_coverage, complete=False),
    )
    profile = Profile(
        profile_id="TEST_ONLY-real-partial",
        analysis_kind="urban_passenger",
        city_id="kaliningrad",
        passenger_modes=["walk"],
        categories=["school"],
        valid_from="2026-11-10T00:00:00Z",
        valid_to="2026-11-11T00:00:00Z",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=canonical_hash(snapshot.model_dump(mode="json")),
        graph_fingerprint="0" * 64,
        routing_profile_fingerprint="0" * 64,
        max_route_pairs=10000,
    )
    return snapshot, facts, profile


def bind_fingerprints(profile, facts):
    graph, costing = input_fingerprints(profile, facts, ProductRegistry())
    return profile.model_copy(
        update={"graph_fingerprint": graph, "routing_profile_fingerprint": costing}
    )


def test_all_accepted_real_school_ids_do_not_prove_full_source_catalog():
    snapshot, facts, profile = real_partial_school_inputs()
    assert len(facts.coverage[0].catalog_destination_ids) == 77
    facts = facts.model_copy(
        update={"coverage": tuple(replace(c, catalog_complete=True) for c in facts.coverage)}
    )
    with pytest.raises(ValueError, match="PARTIAL_SOURCE_COVERAGE"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_all_accepted_real_points_do_not_erase_excluded_positive_population():
    snapshot, facts, profile = real_partial_school_inputs()
    assert facts.population_coverage.excluded_population > 0
    facts = facts.model_copy(
        update={"population_coverage": replace(facts.population_coverage, complete=True)}
    )
    with pytest.raises(ValueError, match="EXCLUDED_OR_UNKNOWN_POPULATION"):
        validate_inputs(bind_fingerprints(profile, facts), facts, snapshot, ProductRegistry())


def test_serving_object_outside_city_is_bound_by_source_scope(transport_facts):
    snapshot, facts, profile = transport_facts
    original = snapshot.destinations[0]
    external = original.model_copy(update={"territory_id": "TEST_ONLY-external-territory"})
    canonical = snapshot.model_copy(
        update={
            "destinations": [external],
            "coverage_by_mode_date_category": [
                scope.model_copy(update={"completeness": "full"})
                for scope in snapshot.coverage_by_mode_date_category
            ],
        }
    )
    destination = replace(facts.destinations[0], territory_id=external.territory_id)
    facts = facts.model_copy(
        update={
            "destinations": (destination,),
            "coverage": tuple(replace(c, catalog_complete=True) for c in facts.coverage),
        }
    )
    profile = profile.model_copy(update={"valid_to": datetime(2026, 11, 30, 20, tzinfo=UTC)})
    validate_inputs(bind_fingerprints(profile, facts), facts, canonical, ProductRegistry())


def test_input_fingerprint_is_identical_across_process_hash_seeds(tmp_path, transport_facts):
    _, facts, profile = transport_facts
    facts = facts.model_copy(
        update={
            "destinations": (
                replace(
                    facts.destinations[0],
                    categories=frozenset(
                        {"school", "airport", "adult_polyclinic", "shopping_center"}
                    ),
                ),
            )
        }
    )
    document = tmp_path / "TEST_ONLY-inputs.json"
    document.write_text(
        InputDocument(schema_version="waydex-engine-inputs/1", inputs=facts).model_dump_json()
    )
    profile_path = tmp_path / "TEST_ONLY-profile.json"
    profile_path.write_text(profile.model_dump_json())
    script = "from pathlib import Path;import sys;from a2_api.adapters.product_inputs import InputDocument;from a2_api.adapters.product_runtime import Profile,input_fingerprints;from a2_api.core.registry import ProductRegistry;print(input_fingerprints(Profile.model_validate_json(Path(sys.argv[2]).read_bytes()),InputDocument.model_validate_json(Path(sys.argv[1]).read_bytes()).inputs,ProductRegistry())[0])"
    outputs = []
    for seed in ("1", "137", "random"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script, str(document), str(profile_path)],
                cwd=ROOT,
                env=env,
                text=True,
            ).strip()
        )
    assert len(set(outputs)) == 1
    assert outputs[0] == input_fingerprints(profile, facts, ProductRegistry())[0]
