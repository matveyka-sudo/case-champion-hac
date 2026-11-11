"""Cargo handling experiments change engine inputs, never saved numerical effects."""

from datetime import timedelta

import pytest
from a2_api.adapters.product_scenario_inputs import source_inputs_hash
from a2_contracts.common import canonical_hash
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult
from test_product_scenarios import delivery_case, with_claim


def handling_case():
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument

    context, facts, _, baseline, frozen = delivery_case()
    document = CargoScenarioDocument.model_validate(
        {
            "schema_version": "waydex-product-cargo-scenarios/1",
            "profile_id": "TEST_ONLY-cargo-profile",
            "snapshot_id": context.snapshot_id,
            "snapshot_hash": context.snapshot_hash,
            "request_hash": canonical_hash(context.normalized_request.model_dump(mode="json")),
            "normalized_request": context.normalized_request.model_dump(mode="json"),
            "source_inputs_hash": source_inputs_hash(facts),
            "resources": [
                {
                    "resource_id": "TEST_ONLY-crane",
                    "cargo_profile_id": facts.profile.profile_id,
                    "compatible_action_ids": ["unload"],
                    "available_units": 2,
                    "valid_from": context.normalized_request.departure_at,
                    "valid_to": context.normalized_request.departure_at + timedelta(days=3),
                    "evidence_refs": ["synthetic-proof"],
                }
            ],
            "plans": [
                {
                    "template_id": "reduce_handling_wait_v1",
                    "operation_type": "reduce_handling_wait",
                    "changes": [
                        {
                            "action_id": "unload",
                            "before_seconds": 7200,
                            "after_seconds": 3600,
                            "resource_id": "TEST_ONLY-crane",
                            "resource_units_before": 1,
                            "resource_units_after": 2,
                            "valid_from": context.normalized_request.departure_at,
                            "valid_to": context.normalized_request.departure_at + timedelta(days=3),
                        }
                    ],
                    "demand_evidence": ["synthetic-proof"],
                    "resource_evidence": ["synthetic-proof"],
                    "assumptions": ["TEST_ONLY"],
                }
            ],
        }
    )
    return context, facts, document, baseline, frozen


def test_handling_change_recalculates_public_engine_and_preserves_baseline():
    from a2_api.adapters import cargo_scenarios
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.db.validation import _validate_scenario_semantics

    case = handling_case()
    claim, facts, document, baseline, _ = with_claim(case)
    before = baseline.model_dump(mode="json")
    result = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    assert isinstance(result, ScenarioResult)
    assert claim.context.scenario_proposal.operation_type == "reduce_handling_wait"
    assert result.service_changes == []
    assert result.handling_changes[0].action_id == "unload"
    old = next(m.value for m in result.before.metrics if m.metric_id == "delivery-overall-time")
    new = next(m.value for m in result.after.metrics if m.metric_id == "delivery-overall-time")
    assert old - new == 3600
    assert any(d.delta == 3600 and d.outcome == "improved" for d in result.deltas)
    assert baseline.model_dump(mode="json") == before
    _validate_scenario_semantics(result, baseline, claim.context.scenario_proposal)
    assert (
        cargo_scenarios.CargoScenarioDocument.model_validate(document.model_dump(mode="json"))
        == document
    )


@pytest.mark.parametrize("attack", ["source", "profile", "snapshot", "baseline", "time"])
def test_handling_plan_rejects_unbound_source_or_saved_baseline(attack):
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.core.errors import DomainError

    context, facts, document, baseline, _ = handling_case()
    profile_id = document.profile_id
    if attack == "source":
        facts = facts.model_copy(update={"readiness": ()})
    elif attack == "profile":
        profile_id = "FOREIGN"
    elif attack == "snapshot":
        document = document.model_copy(update={"snapshot_hash": "f" * 64})
    elif attack == "baseline":
        baseline = baseline.model_copy(update={"result_hash": "changed"})
    else:
        context = context.model_copy(
            update={
                "normalized_request": context.normalized_request.model_copy(
                    update={
                        "departure_at": context.normalized_request.departure_at
                        + timedelta(seconds=1)
                    }
                )
            }
        )
    with pytest.raises(DomainError):
        bridge.propose(context, facts, document, baseline, profile_id=profile_id)


@pytest.mark.parametrize("attack", ["movement", "before", "resource", "window", "evidence"])
def test_handling_plan_requires_actual_action_resource_and_proof(attack):
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
    from a2_api.core.errors import DomainError

    context, facts, document, baseline, _ = handling_case()
    payload = document.model_dump(mode="json")
    change = payload["plans"][0]["changes"][0]
    if attack == "movement":
        change.update(action_id="rail", before_seconds=43200)
    elif attack == "before":
        change["before_seconds"] = 7199
    elif attack == "resource":
        change["resource_units_after"] = 1
    elif attack == "window":
        change["valid_to"] = (
            context.normalized_request.departure_at + timedelta(minutes=1)
        ).isoformat()
    else:
        payload["plans"][0]["resource_evidence"] = ["FOREIGN"]
    with pytest.raises((ValueError, DomainError)):
        changed = CargoScenarioDocument.model_validate(payload)
        bridge.propose(context, facts, changed, baseline, profile_id=document.profile_id)


@pytest.mark.parametrize("attack", ["unknown_resource", "excess_units"])
def test_handling_plan_cannot_verify_arbitrary_resource_or_capacity(attack):
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
    from a2_api.core.errors import DomainError

    context, facts, document, baseline, _ = handling_case()
    payload = document.model_dump(mode="json")
    change = payload["plans"][0]["changes"][0]
    if attack == "unknown_resource":
        change["resource_id"] = "FOREIGN"
    else:
        change["resource_units_after"] = 1000000000
    changed = CargoScenarioDocument.model_validate(payload)
    with pytest.raises(DomainError, match="CARGO_HANDLING_RESOURCE"):
        bridge.propose(context, facts, changed, baseline, profile_id=document.profile_id)


@pytest.mark.parametrize(
    "attack", ["missing", "profile", "action", "capacity", "window", "evidence"]
)
def test_handling_plan_requires_compatible_pinned_resource_fact(attack):
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
    from a2_api.core.errors import DomainError

    context, facts, document, baseline, _ = handling_case()
    payload = document.model_dump(mode="json")
    resource = payload["resources"][0]
    if attack == "missing":
        payload.pop("resources")
    elif attack == "profile":
        resource["cargo_profile_id"] = "FOREIGN"
    elif attack == "action":
        resource["compatible_action_ids"] = ["load"]
    elif attack == "capacity":
        resource["available_units"] = 1
    elif attack == "window":
        resource["valid_to"] = (
            context.normalized_request.departure_at + timedelta(hours=6)
        ).isoformat()
        payload["plans"][0]["changes"][0]["valid_to"] = resource["valid_to"]
    else:
        resource["evidence_refs"] = ["FOREIGN"]
    with pytest.raises((ValueError, DomainError)):
        changed = CargoScenarioDocument.model_validate(payload)
        bridge.propose(context, facts, changed, baseline, profile_id=document.profile_id)


def test_cargo_does_not_masquerade_as_whole_trip_schedule_change():
    from a2_api.adapters import product_scenarios as bridge

    context, facts, document, baseline, _ = handling_case()
    proposal = bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)[0]
    payload = proposal.model_dump(mode="json")
    payload["operation_type"] = "coordinate_transfer"
    with pytest.raises(ValueError):
        ScenarioProposal.model_validate(payload)


def test_handling_calculation_rejects_unbound_job_command():
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.core.errors import DomainError

    claim, facts, document, baseline, _ = with_claim(handling_case())
    claim = claim.model_copy(
        update={"request": claim.request.model_copy(update={"scenario_id": "FOREIGN"})}
    )
    with pytest.raises(DomainError, match="SAVED_SCENARIO_PROPOSAL_REQUIRED"):
        bridge.calculate(
            claim,
            baseline=baseline,
            inputs=facts,
            document=document,
            profile_id=document.profile_id,
        )


def test_handling_result_cannot_publish_against_passenger_context():
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.core.errors import DomainError
    from a2_api.db.validation import _validate_scenario_semantics

    claim, facts, document, baseline, _ = with_claim(handling_case())
    result = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    baseline = baseline.model_copy(update={"analysis_kind": "urban_passenger"})
    with pytest.raises(DomainError, match="CARGO_HANDLING_CONTEXT_UNSUPPORTED"):
        _validate_scenario_semantics(result, baseline, claim.context.scenario_proposal)


def test_handling_result_cannot_publish_against_pipeline_request():
    from a2_api.adapters import product_scenarios as bridge
    from a2_api.core.errors import DomainError
    from a2_api.db.validation import _validate_scenario_semantics

    claim, facts, document, baseline, _ = with_claim(handling_case())
    result = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    baseline = baseline.model_copy(
        update={
            "request": baseline.request.model_copy(
                update={"cargo_transport": "pipeline", "pipeline_product": "oil"}
            )
        }
    )
    with pytest.raises(DomainError, match="CARGO_HANDLING_CONTEXT_UNSUPPORTED"):
        _validate_scenario_semantics(result, baseline, claim.context.scenario_proposal)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_kind", ["pipeline", "passenger"])
async def test_proposal_repository_rejects_non_multimodal_cargo_before_insert(
    monkeypatch, request_kind
):
    from a2_api.core.errors import DomainError
    from a2_api.db.repositories.scenarios import ScenariosRepository
    from test_product_scenarios import make_case

    claim, _, _, baseline, _ = with_claim(handling_case())
    changed_request = (
        baseline.request.model_copy(
            update={"cargo_transport": "pipeline", "pipeline_product": "oil"}
        )
        if request_kind == "pipeline"
        else make_case()[3].request
    )
    baseline = baseline.model_copy(update={"request": changed_request})
    repository = ScenariosRepository(None)

    async def read(*args):
        return baseline

    monkeypatch.setattr(repository, "_read_run", read)
    with pytest.raises(DomainError, match="CARGO_HANDLING_CONTEXT_UNSUPPORTED"):
        await repository._save_proposals(
            object(), [claim.context.scenario_proposal], claim.context.access_scope
        )


def test_cargo_catalog_loads_without_routing_and_finishes_analysis_with_saved_proposal(
    tmp_path, monkeypatch
):
    from a2_api.adapters import cargo_scenarios, product_scenario_runtime
    from a2_api.adapters.product_runtime import FileRef
    from a2_api.core.jobs import CalculationOutput
    from test_product_runtime import write_document
    from test_product_scenario_runtime import cargo_snapshot

    context, facts, document, baseline, _ = handling_case()
    snapshot = cargo_snapshot()
    snapshot_hash = canonical_hash(snapshot.model_dump(mode="json"))
    bound = document.model_copy(update={"snapshot_hash": snapshot_hash})
    reference = FileRef.model_validate(
        write_document(tmp_path / "cargo.json", bound.model_dump(mode="json"))
    )
    with monkeypatch.context() as no_route:
        no_route.setattr(
            cargo_scenarios,
            "analyze_intercity_cargo",
            lambda *a, **k: pytest.fail("catalog routed"),
        )
        loaded = product_scenario_runtime.load_profile_documents(
            tmp_path,
            [reference],
            profile_id=bound.profile_id,
            snapshot=snapshot,
            inputs=facts,
            budget=[1024 * 1024],
            request_allowed=lambda request: request == context.normalized_request,
        )
    assert loaded == (bound,)
    claim, _, _, _, _ = with_claim(handling_case())
    claim = claim.model_copy(update={"kind": "analysis", "context": context})
    output = product_scenario_runtime.finish_analysis(
        claim,
        baseline,
        facts,
        profile_id=document.profile_id,
        documents=[document],
        configured=True,
    )
    assert isinstance(output, CalculationOutput)
    assert output.analysis == baseline
    assert len(output.proposals) == 1


def test_cargo_resource_fact_requires_verified_canonical_evidence_in_loader(tmp_path):
    from a2_api.adapters import product_scenario_runtime
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
    from a2_api.adapters.product_runtime import FileRef
    from test_product_runtime import write_document
    from test_product_scenario_runtime import cargo_snapshot

    context, facts, document, _, _ = handling_case()
    snapshot = cargo_snapshot()
    payload = document.model_dump(mode="json")
    payload["snapshot_hash"] = canonical_hash(snapshot.model_dump(mode="json"))
    payload["resources"][0]["evidence_refs"] = ["document-only-proof"]
    payload["plans"][0]["resource_evidence"] = ["document-only-proof"]
    evidence = snapshot.evidence[0].model_dump(mode="json")
    evidence.pop("verified", None)
    evidence.pop("source_version", None)
    # A private document's reference does not promote an unregistered resource fact.
    payload["evidence_refs"] = [
        {
            key: value
            for key, value in evidence.items()
            if key in {"evidence_id", "source_id", "url", "retrieved_at", "observed_at", "section"}
        }
        | {"evidence_id": "document-only-proof", "observed_at": None, "section": "TEST_ONLY"}
    ]
    changed = CargoScenarioDocument.model_validate(payload)
    reference = FileRef.model_validate(
        write_document(tmp_path / "cargo.json", changed.model_dump(mode="json"))
    )
    with pytest.raises(ValueError, match="VERIFIED_CANONICAL_EVIDENCE_REQUIRED"):
        product_scenario_runtime.load_profile_documents(
            tmp_path,
            [reference],
            profile_id=document.profile_id,
            snapshot=snapshot,
            inputs=facts,
            budget=[1024 * 1024],
            request_allowed=lambda request: request == context.normalized_request,
        )


def test_urban_handling_is_explicitly_unsupported_until_engine_models_processing():
    from a2_api.adapters.cargo_scenarios import CargoScenarioDocument
    from test_product_scenarios import make_case

    _, _, document, _, _ = handling_case()
    context, _, _, _, _ = make_case()
    request = context.normalized_request.model_dump(mode="json")
    payload = document.model_dump(mode="json") | {
        "normalized_request": request,
        "snapshot_id": request["snapshot_id"],
        "request_hash": canonical_hash(request),
    }
    with pytest.raises(ValueError, match="CARGO_HANDLING_MODEL_UNAVAILABLE"):
        CargoScenarioDocument.model_validate(payload)


@pytest.mark.asyncio
async def test_cargo_handling_persists_exact_operation_and_is_scoped_idempotent(database):
    from a2_api.core.errors import DomainError
    from test_product_scenario_publication import queued_case

    pool, _, _ = database
    repository, claim, candidate, baseline = await queued_case(pool, handling_case())
    before_bytes = baseline.model_dump_json()
    altered = candidate.model_copy(
        update={
            "handling_changes": [
                candidate.handling_changes[0].model_copy(update={"after_seconds": 3500})
            ]
        }
    )
    with pytest.raises(DomainError, match="frozen operation"):
        await repository.succeed(claim, altered)
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    await repository.succeed(claim, candidate)
    stored = await repository.get_run(candidate.run_id, claim.context.access_scope)
    assert stored.handling_changes == candidate.handling_changes
    assert stored.before.metrics == baseline.metrics
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).model_dump_json() == before_bytes
    replay = await repository.replay(
        "scenario", claim.request, "scenario", claim.context.access_scope
    )
    assert replay.status == "succeeded" and replay.job_id == claim.job.job_id
    assert (
        await repository.get_job(replay.job_id, claim.context.access_scope)
    ).run_id == stored.run_id
    cached = await repository.enqueue(
        "scenario",
        claim.request,
        claim.context,
        "cached",
        "trace",
        scope=claim.context.access_scope,
    )
    assert cached.status == "succeeded"
    assert (
        await repository.get_job(cached.job_id, claim.context.access_scope)
    ).run_id == stored.run_id
    with pytest.raises(DomainError) as denied:
        await repository.get_run(stored.run_id, "TEST_ONLY-foreign")
    assert denied.value.status_code == 404
