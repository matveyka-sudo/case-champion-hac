"""TEST_ONLY pinned documents, read-only saved baselines and real engine dispatch."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2_api.adapters import product
from a2_api.adapters import product_scenario_runtime as runtime
from a2_api.adapters.product_inputs import InputDocument
from a2_api.adapters.product_runtime import (
    FileRef,
    Profile,
    input_fingerprints,
    load_runtime,
    registry,
)
from a2_api.adapters.product_scenario_inputs import ScenarioDocument, validate_source
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput
from a2_api.core.registry import ProductRegistry
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.requests import DeliveryCargoProfile
from a2_contracts.v2.scenarios import ScenarioRunOptions
from test_product_runtime import transport_facts as transport_facts
from test_product_runtime import write_document
from test_product_scenario_publication import queued_case
from test_product_scenarios import delivery_case, intercity_case, make_case, with_claim

ROOT = Path(__file__).resolve().parents[3]


def cargo_snapshot():
    """Accepted TEST_ONLY finite cargo graph; no production source claims."""
    payload = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())[
        "payload"
    ]
    territory, node, site, evidence = (
        payload["territories"][0],
        payload["access_nodes"][0],
        payload["freight_sites"][0],
        payload["evidence"][0],
    )
    for key, value in payload.items():
        if isinstance(value, list):
            payload[key] = []
    proof = ["synthetic-proof"]
    payload.update(
        snapshot_id="snapshot",
        geographic_scope=["territory-A", "territory-B"],
        territories=[
            dict(territory, territory_id=identifier, source_refs=proof)
            for identifier in ("territory-A", "territory-B")
        ],
        access_nodes=[
            dict(node, node_id=identifier, territory_id=territory_id, source_refs=proof)
            for identifier, territory_id in (
                ("A", "territory-A"),
                ("X", "territory-A"),
                ("B", "territory-B"),
            )
        ],
        freight_sites=[
            dict(
                site,
                site_id="TEST_ONLY-warehouse",
                territory_id="territory-A",
                access_node_id="A",
                source_refs=proof,
            )
        ],
        evidence=[evidence, dict(evidence, evidence_id=proof[0])],
        provenance=dict(
            payload["provenance"],
            graph_version="cargo-graph",
            calendar_version="calendar",
            population_version=None,
        ),
    )
    return AcceptedData.model_validate(payload)


def bound_case():
    context, facts, document, baseline, frozen = delivery_case()
    snapshot = cargo_snapshot()
    snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
    versions = context.versions.model_copy(
        update={"graph_version": "cargo-graph", "population_version": None}
    )
    context = context.model_copy(update={"snapshot_hash": snapshot_hash, "versions": versions})
    document = ScenarioDocument.model_validate(
        document.model_dump(mode="json")
        | {
            "snapshot_hash": snapshot_hash,
            "normalized_request": context.normalized_request.model_dump(mode="json"),
        }
    )
    # Projection metadata follows the real queued source; numerical operands unchanged.
    baseline = baseline.model_copy(update={"versions": versions})
    baseline = baseline.model_copy(
        update={
            "result_hash": canonical_hash(baseline.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
    return snapshot, (context, facts, document, baseline, frozen)


def catalog(tmp_path, document):
    return FileRef.model_validate(
        write_document(tmp_path / "TEST_ONLY-scenario.json", document.model_dump(mode="json"))
    )


def load_catalog(tmp_path, reference, snapshot, facts, document, **overrides):
    arguments = dict(
        profile_id=document.profile_id,
        snapshot=snapshot,
        inputs=facts,
        budget=[1024 * 1024],
        request_allowed=lambda request: request == document.normalized_request,
    )
    arguments.update(overrides)
    return runtime.load_profile_documents(tmp_path, [reference], **arguments)


def test_catalog_source_binding_is_validated_without_calculating(tmp_path, monkeypatch):
    snapshot, (_, facts, document, _, _) = bound_case()
    reference = catalog(tmp_path, document)
    monkeypatch.setattr(runtime.bridge, "propose", lambda *a, **k: pytest.fail("load routed"))
    assert load_catalog(tmp_path, reference, snapshot, facts, document) == (document,)
    assert runtime.template_ids({document.profile_id: (document,)}) == {"coordinate_transfer_v1"}


@pytest.mark.parametrize(
    "attack",
    [
        "bytes",
        "path",
        "budget",
        "request",
        "request_hash",
        "request_offset",
        "naive_request",
        "source",
        "scope",
        "site",
        "evidence",
    ],
)
def test_mutated_or_unbound_catalog_is_rejected(tmp_path, attack):
    snapshot, (_, facts, document, _, _) = bound_case()
    if attack == "request":
        document = document.model_copy(update={"normalized_request": None})
    elif attack == "request_hash":
        document = document.model_copy(update={"request_hash": "f" * 64})
    elif attack in {"request_offset", "naive_request"}:
        request = document.normalized_request
        departure = (
            request.departure_at.replace(tzinfo=None)
            if attack == "naive_request"
            else request.departure_at.astimezone(timezone(timedelta(hours=3)))
        )
        # Even equivalent request semantics cannot rewrite a signed document.
        document = document.model_copy(
            update={"normalized_request": request.model_copy(update={"departure_at": departure})}
        )
    elif attack == "source":
        document = document.model_copy(update={"source_inputs_hash": "f" * 64})
    elif attack == "site":
        job = document.inputs.cargo[0]
        job = replace(job, physical_sites=(replace(job.physical_sites[0], site_id="FOREIGN"),))
        document = document.model_copy(update={"inputs": replace(document.inputs, cargo=(job,))})
    elif attack == "evidence":
        plan = document.plans[0]
        plan = plan.model_copy(
            update={"operation": replace(plan.operation, evidence_refs=("FOREIGN",))}
        )
        document = document.model_copy(update={"plans": (plan,)})
    reference = catalog(tmp_path, document)
    options = {}
    if attack == "bytes":
        (tmp_path / reference.path).write_text("{}")
    elif attack == "path":
        reference = reference.model_copy(update={"path": "../outside.json"})
    elif attack == "budget":
        options["budget"] = [1]
    elif attack == "scope":
        options["request_allowed"] = lambda request: False
    with pytest.raises(ValueError):
        load_catalog(tmp_path, reference, snapshot, facts, document, **options)


def test_only_worker_verified_proposals_are_published_and_bad_optional_plan_preserves_baseline():
    _, case = bound_case()
    claim, facts, document, baseline, _ = with_claim(case)
    claim = claim.model_copy(
        update={
            "kind": "analysis",
            "request": claim.context.normalized_request,
            "context": claim.context.model_copy(update={"scenario_proposal": None}),
        }
    )
    output = runtime.finish_analysis(
        claim,
        baseline,
        facts,
        profile_id=document.profile_id,
        documents=(document,),
        configured=True,
    )
    assert (
        isinstance(output, CalculationOutput)
        and output.analysis == baseline
        and len(output.proposals) == 1
    )
    bad = document.model_copy(update={"source_inputs_hash": "f" * 64})
    limited = runtime.finish_analysis(
        claim, baseline, facts, profile_id=document.profile_id, documents=(bad,), configured=True
    )
    assert limited.metrics == baseline.metrics and limited.scores == baseline.scores
    assert "SCENARIO_PROPOSAL_UNAVAILABLE" in limited.limitations
    assert limited.result_hash == canonical_hash(
        limited.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.parametrize("factory", [make_case, intercity_case, delivery_case])
@pytest.mark.parametrize("offset_hours", [3, -7])
def test_offset_equivalent_request_generates_and_recalculates_same_saved_before(
    monkeypatch, factory, offset_hours
):
    context, facts, document, baseline, frozen = factory()
    document = ScenarioDocument.model_validate(
        document.model_dump(mode="json")
        | {"normalized_request": context.normalized_request.model_dump(mode="json")}
    )
    claim, _, _, _, _ = with_claim((context, facts, document, baseline, frozen))
    request = context.normalized_request.model_copy(
        update={
            "departure_at": context.normalized_request.departure_at.astimezone(
                timezone(timedelta(hours=offset_hours))
            )
        }
    )
    assert request == document.normalized_request
    assert canonical_hash(request.model_dump(mode="json")) != document.request_hash
    document_bytes, baseline_bytes = document.model_dump_json(), baseline.model_dump_json()
    context = claim.context.model_copy(update={"normalized_request": request})
    analysis_claim = claim.model_copy(
        update={
            "kind": "analysis",
            "request": request,
            "context": context.model_copy(update={"scenario_proposal": None}),
        }
    )
    output = runtime.finish_analysis(
        analysis_claim,
        baseline,
        facts,
        profile_id=document.profile_id,
        documents=(document,),
        configured=True,
    )
    assert isinstance(output, CalculationOutput) and len(output.proposals) == 1
    assert "scenario-document:" + canonical_hash(document.model_dump(mode="json")) in (
        output.proposals[0].assumptions
    )

    async def saved_baseline(_):
        return baseline

    monkeypatch.setattr(runtime, "_saved_baseline", saved_baseline)
    result = runtime.calculate_saved(
        claim.model_copy(update={"context": context}),
        facts,
        profile_id=document.profile_id,
        documents=(document,),
    )
    assert result.before.metrics == baseline.metrics and result.before.scores == baseline.scores
    assert baseline.model_dump_json() == baseline_bytes
    assert document.model_dump_json() == document_bytes


@pytest.mark.parametrize("change", ["instant", "date", "priority", "snapshot"])
def test_semantic_request_matching_keeps_changed_scope_rejected(change):
    context, facts, document, baseline, _ = make_case()
    document = ScenarioDocument.model_validate(
        document.model_dump(mode="json")
        | {"normalized_request": context.normalized_request.model_dump(mode="json")}
    )
    request = context.normalized_request.model_copy(deep=True)
    if change in {"instant", "date"}:
        request.departure_at += timedelta(minutes=15) if change == "instant" else timedelta(days=1)
    elif change == "priority":
        category = request.categories[0]
        request.priorities[category] = "high" if request.priorities[category] != "high" else "low"
    else:
        request.snapshot_id = "TEST_ONLY-other-snapshot"
    context = context.model_copy(update={"normalized_request": request})
    with pytest.raises(ValueError, match="SCENARIO_SOURCE_BINDING_MISMATCH"):
        validate_source(document, context, facts, profile_id=document.profile_id)
    claim, _, _, _, _ = with_claim(make_case())
    output = runtime.finish_analysis(
        claim.model_copy(update={"kind": "analysis", "request": request, "context": context}),
        baseline,
        facts,
        profile_id=document.profile_id,
        documents=(document,),
        configured=True,
    )
    assert output is baseline


def test_catalog_rejects_duplicate_requests_with_equivalent_offsets(tmp_path):
    snapshot, (context, facts, document, _, _) = bound_case()
    first = catalog(tmp_path, document)
    request = context.normalized_request.model_copy(
        update={
            "departure_at": context.normalized_request.departure_at.astimezone(
                timezone(timedelta(hours=3))
            )
        }
    )
    equivalent = ScenarioDocument.model_validate(
        document.model_dump(mode="json")
        | {
            "normalized_request": request.model_dump(mode="json"),
            "request_hash": canonical_hash(request.model_dump(mode="json")),
        }
    )
    second = FileRef.model_validate(
        write_document(tmp_path / "TEST_ONLY-equivalent.json", equivalent.model_dump(mode="json"))
    )
    with pytest.raises(ValueError, match="SCENARIO_CATALOG_SOURCE_BINDING_MISMATCH"):
        runtime.load_profile_documents(
            tmp_path,
            [first, second],
            profile_id=document.profile_id,
            snapshot=snapshot,
            inputs=facts,
            budget=[1024 * 1024],
            request_allowed=lambda request: request == document.normalized_request,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", [None, "missing", "mutated", "foreign", "proposal", "claim"])
async def test_readonly_saved_baseline_is_authorized_and_immutable(database, attack):
    pool, _, _ = database
    _, case = bound_case()
    repository, claim, _, baseline = await queued_case(pool, case)
    if attack == "missing":
        await pool.execute("DELETE FROM jobs WHERE run_id=$1", baseline.analysis_id)
        await pool.execute(
            "DELETE FROM scenario_proposals WHERE baseline_analysis_id=$1", baseline.analysis_id
        )
        await pool.execute("DELETE FROM runs WHERE run_id=$1", baseline.analysis_id)
    elif attack == "mutated":
        await pool.execute(
            "UPDATE runs SET result_hash='FOREIGN' WHERE run_id=$1", baseline.analysis_id
        )
    elif attack == "foreign":
        await pool.execute(
            "UPDATE runs SET access_scope='FOREIGN' WHERE run_id=$1", baseline.analysis_id
        )
    elif attack == "proposal":
        await pool.execute(
            "UPDATE scenario_proposals SET content_hash='FOREIGN' WHERE scenario_id=$1",
            claim.request.scenario_id,
        )
    elif attack == "claim":
        claim = claim.model_copy(update={"claim_token": "FOREIGN"})
    if attack is not None:
        with pytest.raises(DomainError):
            await repository.load_scenario_baseline(claim)
    else:
        saved = await repository.load_scenario_baseline(claim)
        assert saved == baseline
        saved.limitations.append("TEST_ONLY-detached")
        assert (
            await repository.get_run(baseline.analysis_id, claim.context.access_scope)
        ).limitations == baseline.limitations
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0


@pytest.mark.asyncio
async def test_saved_baseline_lookup_has_readonly_repeatable_read_snapshot(database, monkeypatch):
    pool, _, _ = database
    _, case = bound_case()
    repository, claim, _, baseline = await queued_case(pool, case)
    original = repository._read_run
    observed = []

    async def read_run(connection, *args, **kwargs):
        observed.append(
            (
                await connection.fetchval("SHOW transaction_read_only"),
                await connection.fetchval("SHOW transaction_isolation"),
            )
        )
        return await original(connection, *args, **kwargs)

    monkeypatch.setattr(repository, "_read_run", read_run)
    assert await repository.load_scenario_baseline(claim) == baseline
    assert observed == [("on", "repeatable read")]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_child_pool_is_closed_before_calculation_even_on_failure(monkeypatch, fail):
    _, case = bound_case()
    claim, _, _, baseline, _ = with_claim(case)
    events = []

    class Pool:
        async def close(self):
            events.append("close")

    async def open_pool(settings):
        events.append("open")
        return Pool()

    class Repository:
        def __init__(self, pool):
            pass

        async def load_scenario_baseline(self, saved):
            assert saved == claim
            events.append("read")
            if fail:
                raise ValueError("TEST_ONLY-private-provider-detail")
            return baseline

    monkeypatch.setattr(
        runtime, "RuntimeSettings", lambda: SimpleNamespace(database_url="TEST_ONLY")
    )
    monkeypatch.setattr(runtime, "open_pool", open_pool)
    monkeypatch.setattr(runtime, "JobRepository", Repository)
    if fail:
        with pytest.raises(DomainError, match="SAVED_SCENARIO_BASELINE_UNAVAILABLE") as unavailable:
            await runtime._saved_baseline(claim)
        assert "private-provider" not in str(unavailable.value)
    else:
        assert await runtime._saved_baseline(claim) == baseline
    assert events == ["open", "read", "close"]


def configure_cargo(tmp_path, monkeypatch):
    snapshot, case = bound_case()
    context, facts, document, _, _ = case
    cargo = DeliveryCargoProfile(
        profile_id="food-ambient",
        kind="delivery",
        food_class="food",
        urgency="urgent",
        temperature_requirements=dict(controlled=False, min_c=None, max_c=None),
        handling_requirements=["sealed"],
        vehicle_constraints=dict(max_height_m=3.0, max_weight_tonnes=12.0, restrictions=[]),
    )
    current = ProductRegistry(cargo_profiles=[cargo])
    profile = Profile(
        profile_id=document.profile_id,
        analysis_kind="intercity_cargo",
        origin_id="territory-A",
        destination_id="territory-B",
        cargo_profile_id="food-ambient",
        valid_from="2026-11-01T00:00:00Z",
        valid_to="2026-12-01T00:00:00Z",
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=context.snapshot_hash,
        graph_fingerprint="0" * 64,
        routing_profile_fingerprint="0" * 64,
        max_route_pairs=100,
    )
    graph, costing = input_fingerprints(profile, facts, current)
    supplement = write_document(
        tmp_path / "TEST_ONLY-inputs.json",
        InputDocument(schema_version="waydex-engine-inputs/1", inputs=facts).model_dump(
            mode="json"
        ),
    )
    profile = profile.model_copy(
        update={
            "graph_fingerprint": graph,
            "routing_profile_fingerprint": costing,
            "inputs": FileRef.model_validate(supplement),
            "scenarios": [catalog(tmp_path, document)],
        }
    )
    snapshot_ref = write_document(
        tmp_path / "TEST_ONLY-snapshot.json", snapshot.model_dump(mode="json")
    )
    snapshot_ref.update(snapshot_id=snapshot.snapshot_id, content_hash=context.snapshot_hash)
    manifest = dict(
        schema_version="waydex-product-runtime/1",
        snapshots=[snapshot_ref],
        cargo_profiles=[cargo.model_dump(mode="json")],
        territories=[
            dict(territory_id=t.territory_id, name=t.name, level=t.level, timezone=t.timezone)
            for t in snapshot.territories
        ],
        profiles=[profile.model_dump(mode="json")],
    )
    reference = write_document(tmp_path / "TEST_ONLY-runtime.json", manifest)
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_PATH", str(tmp_path / reference["path"]))
    monkeypatch.setenv("A2_PRODUCT_RUNTIME_SHA256", reference["sha256"])
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY-" + reference["sha256"])
    return snapshot, context.normalized_request


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_hours", [0, 3, -7])
async def test_product_saved_analysis_proposal_and_scenario_full_cycle(
    database, tmp_path, monkeypatch, offset_hours
):
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService

    pool, schema, url = database
    snapshot, request = configure_cargo(tmp_path, monkeypatch)
    request = request.model_copy(
        update={
            "departure_at": request.departure_at.astimezone(timezone(timedelta(hours=offset_hours)))
        }
    )
    monkeypatch.setenv("A2_DATABASE_URL", url)
    monkeypatch.setenv("A2_DATABASE_SCHEMA", schema)
    loaded = load_runtime()
    assert len(loaded.scenario_documents["TEST_ONLY-cargo-profile"]) == 1
    current = registry()
    repository = JobRepository(pool)
    service = AnalysisService(
        current,
        repository,
        code_version=product.RuntimeSettings().code_version,
        engine_enabled=True,
        preflight=product.preflight,
        registered_template_ids=current.scenario_template_ids,
    )
    queued = await service.analyse(request, "base", "trace", "TEST_ONLY-scope")
    claim = await repository.claim("TEST_ONLY-real-product-worker")
    calculated = await asyncio.to_thread(product.calculate, claim)
    assert isinstance(calculated, CalculationOutput) and len(calculated.proposals) == 1
    await repository.succeed(claim, calculated.analysis, proposals=calculated.proposals)
    baseline = await service.analysis(claim.context.analysis_id, "TEST_ONLY-scope")
    before_bytes = baseline.model_dump_json()
    proposals = await service.proposals(baseline.analysis_id, "TEST_ONLY-scope")
    assert len(proposals) == 1
    scenario = await service.scenario(
        proposals[0].scenario_id,
        ScenarioRunOptions(schema_version="2"),
        "scenario",
        "trace",
        "TEST_ONLY-scope",
    )
    scenario_claim = await repository.claim("TEST_ONLY-real-product-worker")
    after = await asyncio.to_thread(product.calculate, scenario_claim)
    await repository.succeed(scenario_claim, after)
    saved = await service.scenario_result(after.run_id, "TEST_ONLY-scope")
    assert saved.baseline_result_hash == baseline.result_hash
    assert saved.before.metrics == baseline.metrics and saved.before.scores == baseline.scores
    assert (
        next(row.value for row in saved.after.metrics if row.metric_id == "delivery-overall-time")
        == 82800
    )
    assert (
        await service.analysis(baseline.analysis_id, "TEST_ONLY-scope")
    ).model_dump_json() == before_bytes
    assert (await service.job(queued.job_id, "TEST_ONLY-scope")).status == "succeeded"
    assert (await service.job(scenario.job_id, "TEST_ONLY-scope")).status == "succeeded"


@pytest.mark.parametrize("offset_hours", [0, 3])
def test_configured_http_and_spawned_worker_publish_saved_product_scenario(
    database_sync_config, tmp_path, monkeypatch, offset_hours
):
    from a2_api.api.bootstrap import build_app
    from a2_api.core.config import RuntimeSettings
    from a2_api.db.connection import open_pool
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.workers.runner import Worker
    from fastapi.testclient import TestClient

    url, schema = database_sync_config
    _, request = configure_cargo(tmp_path, monkeypatch)
    request = request.model_copy(
        update={
            "departure_at": request.departure_at.astimezone(timezone(timedelta(hours=offset_hours)))
        }
    )
    monkeypatch.setenv("A2_DATABASE_URL", url)
    monkeypatch.setenv("A2_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("A2_REGISTRY_HOOK", "a2_api.adapters.product:registry")
    monkeypatch.setenv("A2_PREFLIGHT_HOOK", "a2_api.adapters.product:preflight")
    monkeypatch.setenv("A2_CALCULATION_HANDLER", "a2_api.adapters.product:calculate")
    settings = RuntimeSettings()

    async def worker_once():
        pool = await open_pool(settings)
        try:
            assert await Worker(
                JobRepository(pool),
                handler=settings.calculation_handler,
                code_version=settings.code_version,
                timeout_seconds=15,
            ).run_once()
        finally:
            await pool.close()

    with TestClient(build_app(settings)) as client:
        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["urban_passenger_scopes"] == []
        queued = client.post(
            "/api/v1/analyses",
            json=request.model_dump(mode="json"),
            headers={"Idempotency-Key": "TEST_ONLY-http-base"},
        )
        assert queued.status_code == 202, queued.text
        asyncio.run(worker_once())
        job = client.get("/api/v1/jobs/" + queued.json()["job_id"]).json()
        assert job["status"] == "succeeded", job
        baseline_path = "/api/v1/analyses/" + job["analysis_id"]
        baseline = client.get(baseline_path).json()
        proposals = client.get(baseline_path + "/scenarios")
        assert proposals.status_code == 200 and len(proposals.json()) == 1
        proposal = proposals.json()[0]
        accepted = client.post(
            "/api/v1/scenarios/" + proposal["scenario_id"] + "/runs",
            json={"schema_version": "2"},
            headers={"Idempotency-Key": "TEST_ONLY-http-scenario"},
        )
        assert accepted.status_code == 202, accepted.text
        asyncio.run(worker_once())
        completed = client.get("/api/v1/jobs/" + accepted.json()["job_id"]).json()
        assert completed["status"] == "succeeded", completed
        result_path = "/api/v1/scenario-runs/" + completed["scenario_run_id"]
        scenario = client.get(result_path)
        assert scenario.status_code == 200, scenario.text
        result = scenario.json()
        assert result["baseline_result_hash"] == baseline["result_hash"]
        assert result["before"]["metrics"] == baseline["metrics"]
        assert result["before"]["scores"] == baseline["scores"]
        assert (
            next(
                row["value"]
                for row in result["after"]["metrics"]
                if row["metric_id"] == "delivery-overall-time"
            )
            == 82800
        )
        assert client.get(baseline_path).json() == baseline
        # A different session cannot read either the saved input or its result.
        client.cookies.clear()
        assert client.get(baseline_path).status_code == 404
        assert client.get(result_path).status_code == 404


def test_validated_runtime_scopes_reach_public_registry(tmp_path, monkeypatch, transport_facts):
    from test_product_runtime import configure

    configure(tmp_path, monkeypatch, transport_facts)
    loaded = load_runtime()
    scopes = registry().capabilities().urban_passenger_scopes
    assert scopes == loaded.urban_passenger_scopes
    assert scopes and scopes[0].scope_id == "TEST_ONLY-walk"
    assert scopes[0].categories == ["adult_polyclinic"]
