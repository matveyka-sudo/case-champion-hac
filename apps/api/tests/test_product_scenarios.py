"""TEST_ONLY synthetic facts: immutable real public-engine scenario bridge controls."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from a2_api.adapters import product_scenarios as bridge
from a2_api.adapters.product_inputs import UrbanInputs
from a2_api.adapters.product_projection import project_urban
from a2_api.adapters.product_scenario_inputs import (
    ScenarioDocument,
    ScenarioPlan,
    source_inputs_hash,
    validate_source,
)
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob
from a2_contracts.api import Job
from a2_contracts.common import canonical_hash
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    ScenarioFeasibilityInputs,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResourceBlock,
    TransportModeBinding,
    create_scenario_baseline,
    scenario_hash,
    simulate_scenario,
)
from test_product_projection import example, projection


def make_case(*, loss=False, unknown_population=False):
    module = example("scenario")
    source, incoming, outgoing = module.demo_inputs()
    job, dataset = source.urban[0], source.timetables[0]
    if loss:
        # Target boarding is lost for p2, while the accepted incoming transfer
        # remains possible. A global "transfer got better" cannot hide that loss.
        job = replace(job, points=(replace(job.points[1], population=10),))
        job = replace(
            job, coverage=tuple(row for row in job.coverage if row.scope.point_id == "p2")
        )
        table = replace(
            dataset.timetable,
            runs=tuple(
                replace(
                    run,
                    events=tuple(
                        replace(
                            event,
                            arrival_at=event.arrival_at
                            + timedelta(seconds=-600 if run.run_id == incoming else 360),
                            departure_at=event.departure_at
                            + timedelta(seconds=-600 if run.run_id == incoming else 360),
                        )
                        for event in run.events
                    ),
                )
                for run in dataset.timetable.runs
            ),
        )
        requested_at = module.START + timedelta(seconds=900)
    else:
        table = dataset.timetable
        requested_at = module.START
    if unknown_population:
        job = replace(
            job,
            points=tuple(
                replace(point, population=None) if point.point_id == "p1" else point
                for point in job.points
            ),
        )
    context = replace(job.context, requested_departure_at=requested_at)
    population = replace(
        job.population_coverage,
        context=context,
        complete=not unknown_population,
        point_ids=frozenset(point.point_id for point in job.points),
    )
    job = replace(
        job,
        context=context,
        coverage=tuple(
            replace(row, scope=replace(row.scope, context=context)) for row in job.coverage
        ),
        population_coverage=population,
        original_cells=(),
        districts=(),
        district_shares=(),
        district_coverage=None,
    )
    physical = tuple(
        PhysicalService(
            run.trip_id,
            (run.run_id,),
            (PhysicalDeparture(0, "TEST_ONLY-departure:" + run.trip_id, module.PROOF),),
            module.PROOF,
        )
        for run in table.runs
    )
    dataset = replace(
        dataset,
        timetable=table,
        transport_modes=(TransportModeBinding("bus", "road", module.PROOF),),
        physical_services=physical,
    )
    facts = UrbanInputs(
        kind="urban_passenger",
        context=job.context,
        points=job.points,
        destinations=job.destinations,
        coverage=job.coverage,
        area=job.area,
        population_coverage=population,
        thresholds=job.thresholds,
        timetable=table,
    )
    inputs = ScenarioInputs(timetables=(dataset,), urban=(job,))
    engine_baseline = create_scenario_baseline(inputs)
    operation = ScenarioOperation(
        engine_baseline.baseline_id,
        "TEST_ONLY-coordinate",
        "coordinate_transfer",
        "timetable",
        dataset.network_id,
        outgoing,
        table.runs[0].service_date,
        "0",
        scenario_hash(next(run for run in table.runs if run.run_id == outgoing)),
        -360 if loss else 180,
        incoming,
        "1",
        module.PROOF,
    )
    out = projection(
        "urban_passenger",
        module.PROOF,
        city_id="city",
        passenger_mode="public_transport",
        categories=list(dict(job.selected_categories)),
        priorities=dict(job.selected_categories),
    )
    out.context.normalized_request.departure_at = requested_at
    project_urban(out, engine_baseline.before.urban[0].analysis)
    baseline = out.finish()
    document = ScenarioDocument(
        schema_version="waydex-product-scenarios/1",
        profile_id="TEST_ONLY-profile",
        snapshot_id=out.context.snapshot_id,
        snapshot_hash=out.context.snapshot_hash,
        request_hash=canonical_hash(out.context.normalized_request.model_dump(mode="json")),
        source_inputs_hash=source_inputs_hash(facts),
        inputs=inputs,
        plans=(ScenarioPlan(template_id="coordinate_transfer_v1", operation=operation),),
    )
    return out.context, facts, document, baseline, engine_baseline


def with_claim(case):
    context, facts, document, baseline, frozen = case
    proposal = bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)[0]
    # Persistence rebinds identity while retaining the complete frozen command.
    proposal = proposal.model_copy(update={"scenario_id": "TEST_ONLY-saved-proposal"})
    request = ScenarioRunRequest(
        schema_version="2",
        scenario_id=proposal.scenario_id,
        baseline_analysis_id=baseline.analysis_id,
    )
    job = Job(
        job_id="TEST_ONLY-job",
        status="running",
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
        started_at=datetime(2026, 11, 9, tzinfo=UTC),
        finished_at=None,
        run_id=None,
        error=None,
        trace_id="TEST_ONLY-trace",
        bundle_id=context.snapshot_id,
        input_hash="TEST_ONLY-input",
        code_version=context.versions.code_version,
    )
    claim = ClaimedJob(
        job=job,
        kind="scenario",
        request=request,
        context=context.model_copy(update={"scenario_proposal": proposal}),
        cache_key="TEST_ONLY-cache",
        claim_token="TEST_ONLY-claim",
    )
    return claim, facts, document, baseline, frozen


def run(case):
    claim, facts, document, baseline, frozen = with_claim(case)
    result = bridge.calculate(
        claim, baseline=baseline, inputs=facts, document=document, profile_id=document.profile_id
    )
    return result, frozen


def test_real_engine_new_connection_negative_effects_and_immutable_saved_before():
    case = make_case()
    before_bytes = case[3].model_dump_json()
    result, frozen = run(case)
    expected = simulate_scenario(frozen, case[2].plans[0].operation)
    assert ScenarioResult.model_validate_json(result.model_dump_json()) == result
    assert result.before.metrics == case[3].metrics and result.before.scores == case[3].scores
    assert case[3].model_dump_json() == before_bytes
    assert any(row.outcome == "new_connection" for row in result.deltas)
    assert any(row.outcome == "worsened" and row.delta < 0 for row in result.deltas)
    assert result.worsened_metric_ids
    assert result.affected_population == expected.impact.known_affected_population == 100
    assert result.feasibility == "unverified" and "TURNAROUND_UNVERIFIED" in result.limitations
    assert result.overlay_graph_id != result.versions.graph_version
    assert run(case)[0] == result


def test_proven_loss_retains_full_scoped_placeholder_and_worsening():
    result, _ = run(make_case(loss=True))
    losses = [
        row for row in result.deltas if row.outcome == "lost_connection" and row.kind == "metric"
    ]
    assert losses
    metric = next(row for row in result.after.metrics if row.metric_id == losses[0].after_id)
    assert metric.value is None and metric.status == "not_applicable"
    assert metric.reason_code == "NO_FEASIBLE_ROUTE" and metric.completeness == "full"
    assert metric.metric_id in result.worsened_metric_ids
    assert (
        metric.subject
        == next(row for row in result.before.metrics if row.metric_id == metric.metric_id).subject
    )


def test_unknown_positive_population_never_reports_exact_affected_count():
    result, _ = run(make_case(unknown_population=True))
    assert result.affected_population is None
    assert result.status == "partial"
    assert "AFFECTED_POPULATION_LOWER_BOUND" in result.limitations
    assert any(row.outcome == "unknown" and row.delta is None for row in result.deltas)


def test_document_json_roundtrip_preserves_source_binding():
    context, facts, document, _, _ = make_case()
    parsed = ScenarioDocument.model_validate_json(document.model_dump_json())
    validate_source(parsed, context, facts, profile_id=document.profile_id)
    assert parsed == document


@pytest.mark.parametrize(
    "attack",
    [
        "baseline",
        "self_consistent_baseline",
        "operation",
        "snapshot",
        "versions",
        "source_population",
        "private_operand",
        "request",
        "profile",
        "tail",
    ],
)
def test_saved_inputs_and_operation_tampering_fail_closed(attack):
    claim, facts, document, baseline, _ = with_claim(make_case())
    profile_id = document.profile_id
    if attack in ("baseline", "self_consistent_baseline"):
        payload = baseline.model_dump(mode="json")
        payload["metrics"][0]["value"] += 1
        if attack == "self_consistent_baseline":
            payload["result_hash"] = canonical_hash(
                {key: value for key, value in payload.items() if key != "result_hash"}
            )
        baseline = type(baseline).model_validate(payload)
    elif attack == "operation":
        command = claim.context.scenario_proposal.model_copy(deep=True)
        command.fixed_parameters.shift_seconds += 1
        claim = claim.model_copy(
            update={"context": claim.context.model_copy(update={"scenario_proposal": command})}
        )
    elif attack == "snapshot":
        document = document.model_copy(update={"snapshot_hash": "TEST_ONLY-altered"})
    elif attack == "versions":
        claim = claim.model_copy(
            update={
                "context": claim.context.model_copy(
                    update={
                        "versions": claim.context.versions.model_copy(
                            update={"code_version": "TEST_ONLY-wrong"}
                        )
                    }
                )
            }
        )
    elif attack == "source_population":
        facts = facts.model_copy(
            update={"points": tuple(replace(point, population=999) for point in facts.points)}
        )
    elif attack == "private_operand":
        document = document.model_copy(
            update={
                "inputs": replace(
                    document.inputs,
                    urban=(
                        replace(
                            document.inputs.urban[0],
                            points=tuple(
                                replace(point, population=999)
                                for point in document.inputs.urban[0].points
                            ),
                        ),
                    ),
                )
            }
        )
    elif attack == "request":
        claim = claim.model_copy(
            update={
                "context": claim.context.model_copy(
                    update={
                        "normalized_request": claim.context.normalized_request.model_copy(
                            update={
                                "departure_at": claim.context.normalized_request.departure_at
                                + timedelta(seconds=1)
                            }
                        )
                    }
                )
            }
        )
    elif attack == "profile":
        profile_id = "TEST_ONLY-other-profile"
    elif attack == "tail":
        document = document.model_copy(
            update={
                "plans": (
                    document.plans[0].model_copy(
                        update={
                            "operation": replace(
                                document.plans[0].operation, timing_model="departure_tail"
                            )
                        }
                    ),
                )
            }
        )
    with pytest.raises(DomainError):
        bridge.calculate(
            claim, baseline=baseline, inputs=facts, document=document, profile_id=profile_id
        )


def test_missing_physical_execution_binding_is_not_inferred_from_service_id():
    context, facts, document, baseline, _ = make_case()
    document = document.model_copy(
        update={
            "inputs": replace(
                document.inputs,
                timetables=(replace(document.inputs.timetables[0], physical_services=()),),
            )
        }
    )
    with pytest.raises(DomainError, match="PHYSICAL_EXECUTION_BINDINGS_REQUIRED"):
        bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)


def test_publication_guard_rejects_hidden_engine_worsening(monkeypatch):
    claim, facts, document, baseline, _ = with_claim(make_case())
    original = bridge.simulate_scenario

    def hide(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(
            result,
            effects=tuple(row for row in result.effects if row.outcome != "worsened"),
            worsened_metric_ids=(),
        )

    monkeypatch.setattr(bridge, "simulate_scenario", hide)
    with pytest.raises(DomainError, match="WORSENING_HIDDEN"):
        bridge.calculate(
            claim,
            baseline=baseline,
            inputs=facts,
            document=document,
            profile_id=document.profile_id,
        )


def resource_case(*, infeasible=False, additional=False):
    context, facts, document, baseline, frozen = make_case()
    plan = document.plans[0]
    operation = plan.operation
    table = frozen.inputs.timetables[0].timetable
    target = next(run for run in table.runs if run.run_id == operation.execution_id)
    if additional:
        new_run = replace(
            target,
            trip_id="TEST_ONLY-additional",
            events=tuple(
                replace(
                    event,
                    arrival_at=event.arrival_at + timedelta(seconds=operation.shift_seconds),
                    departure_at=event.departure_at + timedelta(seconds=operation.shift_seconds),
                )
                for event in target.events
            ),
        )
        identity = PhysicalService(
            "TEST_ONLY-added-physical",
            (new_run.run_id,),
            (PhysicalDeparture(0, "TEST_ONLY-added-departure", operation.evidence_refs),),
            operation.evidence_refs,
        )
        operation = replace(
            operation,
            template_id="additional_departure",
            new_run=new_run,
            new_physical_service=identity,
            demand_evidence_refs=operation.evidence_refs,
        )
        target_id = new_run.run_id
    else:
        target_id = operation.execution_id
    block = ScenarioResourceBlock(
        "TEST_ONLY-bus",
        "network",
        (target_id,),
        table.scope.start_at,
        target.events[-1].arrival_at if infeasible else table.scope.end_at,
        (),
        operation.evidence_refs,
        0,
        0,
    )
    feasibility = ScenarioFeasibilityInputs(
        frozen.baseline_id,
        scenario_hash(operation),
        (block,),
        (target_id,),
        True,
        True,
        True,
        table.scope.start_at,
        table.scope.end_at,
        operation.evidence_refs,
    )
    plan = plan.model_copy(update={"operation": operation, "feasibility": feasibility})
    document = document.model_copy(update={"plans": (plan,)})
    return context, facts, document, baseline, frozen


def test_verified_resource_turnaround_is_checked_by_public_engine():
    result, _ = run(resource_case())
    assert result.feasibility == "verified"
    assert result.resource_evidence and result.turnaround_evidence


def test_infeasible_state_is_preserved_as_explicit_failure():
    context, facts, document, baseline, _ = resource_case(infeasible=True)
    with pytest.raises(DomainError, match="SCENARIO_INFEASIBLE:RESOURCE_AVAILABILITY_VIOLATED"):
        bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)


def test_added_service_requires_verified_physical_resource_and_turnaround():
    context, facts, document, baseline, _ = resource_case(additional=True)
    unverified = document.model_copy(
        update={"plans": (document.plans[0].model_copy(update={"feasibility": None}),)}
    )
    with pytest.raises(DomainError, match="ADDITIONAL_DEPARTURE_REQUIRES_VERIFIED"):
        bridge.propose(context, facts, unverified, baseline, profile_id=document.profile_id)
    result, _ = run(resource_case(additional=True))
    assert result.feasibility == "verified"
    assert result.service_changes[0].service_id == document.plans[0].operation.new_run.run_id


def intercity_case():
    from a2_api.adapters.product_inputs import IntercityInputs
    from a2_api.adapters.product_projection import project_intercity
    from a2_engine import (
        IntercityScenarioInput,
        build_reference,
        load_intercity_passenger_rules,
        load_passenger_config,
    )

    context, urban_facts, document, _, _ = make_case()
    module = example("intercity")
    scope, _ = module.demo_inputs()
    common = replace(scope.comparison_scope, origin_node_ids=("S",), destination_node_ids=("D",))
    scope = replace(scope, comparison_scope=common)
    graph = module.demo_reference(scope, 1200)
    graph = replace(
        graph,
        states=tuple(
            replace(state, node_id="S" if state.node_id == "A" else "D") for state in graph.states
        ),
    )
    reference = build_reference(graph, common)
    dataset = document.inputs.timetables[0]
    start = context.normalized_request.departure_at
    facts = IntercityInputs(
        kind="intercity_passenger",
        scope=scope,
        timetable=urban_facts.timetable,
        transport_modes=dataset.transport_modes,
        physical_services=dataset.physical_services,
        reference_graph=graph,
        reference_scope=common,
        frequency_start_at=start,
        frequency_end_at=start + timedelta(days=7),
    )
    job = IntercityScenarioInput(
        "TEST_ONLY-intercity",
        "network",
        scope,
        start,
        reference,
        load_passenger_config(),
        load_intercity_passenger_rules(),
        start,
        start + timedelta(days=7),
    )
    inputs = ScenarioInputs(timetables=(dataset,), intercity=(job,))
    frozen = create_scenario_baseline(inputs)
    operation = replace(document.plans[0].operation, baseline_id=frozen.baseline_id)
    out = projection(
        "intercity_passenger",
        (*module.PROOF, *document.plans[0].operation.evidence_refs),
        territory_level="city",
        origin_id=common.origin_territory_id,
        destination_id=common.destination_territory_id,
        origin_node_ids=["S"],
        destination_node_ids=["D"],
    )
    out.context.normalized_request.departure_at = start
    project_intercity(out, frozen.before.intercity[0][1], reference)
    baseline = out.finish()
    document = document.model_copy(
        update={
            "request_hash": canonical_hash(out.context.normalized_request.model_dump(mode="json")),
            "source_inputs_hash": source_inputs_hash(facts),
            "inputs": inputs,
            "plans": (document.plans[0].model_copy(update={"operation": operation}),),
        }
    )
    return out.context, facts, document, baseline, frozen


def delivery_case():
    from a2_api.adapters.product_inputs import DeliveryInputs
    from a2_api.adapters.product_projection import project_delivery
    from a2_engine import (
        CargoExecution,
        CargoScenarioInput,
        CargoSite,
        load_cargo_delivery_rules,
        load_freight_config,
    )

    module = example("delivery")
    scope, states, actions, readiness = module.demo_inputs()
    executions = tuple(
        CargoExecution(
            service,
            module.START.date(),
            tuple(action.action_id for action in actions if action.service_id == service),
            module.PROOF,
        )
        for service in ("road-service", "rail-service")
    )
    job = CargoScenarioInput(
        "TEST_ONLY-cargo",
        scope,
        module.START,
        module.PROFILE,
        states,
        actions,
        readiness,
        executions,
        load_freight_config(),
        load_cargo_delivery_rules(),
        physical_sites=(CargoSite("TEST_ONLY-warehouse", module.CONTEXT, module.PROOF),),
    )
    inputs = ScenarioInputs(cargo=(job,))
    frozen = create_scenario_baseline(inputs)
    execution = next(row for row in executions if row.execution_id == "rail-service")
    operation = ScenarioOperation(
        frozen.baseline_id,
        "TEST_ONLY-cargo-operation",
        "coordinate_transfer",
        "cargo",
        job.job_id,
        "rail-service",
        module.START.date(),
        "rail",
        scenario_hash(
            (
                execution,
                tuple(action for action in job.actions if action.action_id in execution.action_ids),
            )
        ),
        -3600,
        "road-service",
        "road",
        module.PROOF,
    )
    out = projection(
        "intercity_cargo",
        module.PROOF,
        territory_level="city",
        origin_id=scope.origin_territory_id,
        destination_id=scope.destination_territory_id,
        cargo_profile_id=module.PROFILE.profile_id,
        cargo_transport="multimodal",
    )
    project_delivery(out, frozen.before.cargo[0][1])
    baseline = out.finish()
    facts = DeliveryInputs(
        kind="intercity_cargo",
        scope=scope,
        profile=module.PROFILE,
        states=states,
        actions=actions,
        readiness=readiness,
    )
    document = ScenarioDocument(
        schema_version="waydex-product-scenarios/1",
        profile_id="TEST_ONLY-cargo-profile",
        snapshot_id=out.context.snapshot_id,
        snapshot_hash=out.context.snapshot_hash,
        request_hash=canonical_hash(out.context.normalized_request.model_dump(mode="json")),
        source_inputs_hash=source_inputs_hash(facts),
        inputs=inputs,
        plans=(ScenarioPlan(template_id="coordinate_transfer_v1", operation=operation),),
    )
    return out.context, facts, document, baseline, frozen


def test_intercity_complete_task_roster_reference_and_new_connection():
    case = intercity_case()
    result, _ = run(case)
    assert any(row.outcome == "new_connection" for row in result.deltas)
    total = next(row for row in result.after.metrics if row.metric_id == "intercity-public-total")
    assert total.value == 1500
    assert result.before.metrics == case[3].metrics
    assert result.affected_population is None and result.affected_sites is None
    assert {row.metric_id for row in result.before.metrics} == {
        row.metric_id for row in result.after.metrics
    }


def test_cargo_real_schedule_improves_complete_delivery_and_tracks_physical_site():
    result, frozen = run(delivery_case())
    time = next(row for row in result.after.metrics if row.metric_id == "delivery-overall-time")
    assert time.value == 82800
    assert result.affected_sites == 1 and result.affected_population is None
    assert any(row.outcome == "improved" and row.kind == "score" for row in result.deltas)
    assert frozen.before.cargo[0][1].overall.best_known.evaluation.total_seconds.value == 86400


def test_infeasible_added_service_cannot_be_published_as_verified_or_unverified():
    context, facts, document, baseline, _ = resource_case(additional=True, infeasible=True)
    with pytest.raises(DomainError, match="SCENARIO_INFEASIBLE:"):
        bridge.propose(context, facts, document, baseline, profile_id=document.profile_id)


def test_no_registered_mutable_model_is_reported_for_pipeline_instead_of_fabricated():
    from a2_api.adapters.product_inputs import PipelineInputs

    context, _, document, _, _ = make_case()
    module = example("pipeline")
    scope, observation = module.demo_inputs()
    facts = PipelineInputs(kind="pipeline", scope=scope, observations=(observation,))
    document = document.model_copy(update={"source_inputs_hash": source_inputs_hash(facts)})
    with pytest.raises(ValueError, match="MUTABLE_TRANSPORT_MODEL_UNAVAILABLE"):
        validate_source(document, context, facts, profile_id=document.profile_id)


def test_private_document_and_source_hashes_are_stable_across_worker_hash_seeds():
    import os
    import subprocess
    import sys
    from pathlib import Path

    command = "from test_product_scenarios import make_case; from a2_api.adapters.product_scenario_inputs import source_inputs_hash; from a2_contracts.common import canonical_hash; _,facts,document,_,_=make_case(); print(source_inputs_hash(facts)); print(canonical_hash(document.model_dump(mode='json')))"
    hashes = []
    for seed in ("1", "21"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = (
            str(Path(__file__).parent) + os.pathsep + environment.get("PYTHONPATH", "")
        )
        hashes.append(
            subprocess.check_output(
                [sys.executable, "-c", command], env=environment, text=True
            ).strip()
        )
    assert hashes[0] == hashes[1]
