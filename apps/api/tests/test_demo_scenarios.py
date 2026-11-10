"""Explicit synthetic-demo plans use the existing production scenario bridge."""

from dataclasses import replace

import pytest
from a2_api.adapters import product_scenarios as bridge
from a2_api.adapters.demo_scenarios import build_demo_scenarios
from a2_api.adapters.demo_transport import DEMO_LIMITATIONS
from a2_api.adapters.product_inputs import UrbanInputs
from a2_api.adapters.product_scenario_inputs import ScenarioDocument
from a2_api.core.jobs import V2JobContext
from a2_contracts.common import canonical_hash
from a2_contracts.v2.results import AnalysisResult
from test_product_scenarios import make_case, with_claim


def generated_case(*, shifts=(180, 360)):
    context, inputs, document, baseline, frozen = make_case()
    table = document.inputs.timetables[0]
    operation = document.plans[0].operation
    generated = build_demo_scenarios(
        context,
        inputs,
        profile_id=document.profile_id,
        incoming_run_id=operation.incoming_execution_id,
        outgoing_run_id=operation.execution_id,
        shift_seconds=shifts,
        transport_modes=table.transport_modes,
        physical_services=table.physical_services,
        evidence_refs=operation.evidence_refs,
    )
    return context, inputs, generated, baseline, frozen


def test_explicit_demo_shifts_publish_allowlisted_proposals_and_real_before_after():
    case = generated_case()
    context, inputs, document, baseline, _ = case
    unchanged = baseline.model_dump_json()
    proposals = bridge.propose(context, inputs, document, baseline, profile_id=document.profile_id)
    assert [row.fixed_parameters.shift_seconds for row in proposals] == [180, 360]
    assert {row.template_id for row in proposals} == {"coordinate_transfer_v1"}
    assert all("DEMO_SYNTHETIC_TRANSPORT_MODEL" in row.assumptions for row in proposals)
    assert all("NOT_A_REAL_CITY_RANKING" in row.assumptions for row in proposals)
    claim, *_ = with_claim(case)
    result = bridge.calculate(
        claim, baseline=baseline, inputs=inputs, document=document, profile_id=document.profile_id
    )
    assert any(row.outcome == "new_connection" for row in result.deltas)
    assert any(row.outcome == "worsened" for row in result.deltas)
    assert result.before.scores == baseline.scores
    assert result.before.metrics == baseline.metrics
    assert baseline.model_dump_json() == unchanged
    assert document == ScenarioDocument.model_validate_json(document.model_dump_json())
    assert result.baseline_result_hash == baseline.result_hash
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )


@pytest.mark.parametrize("shifts", [(), (0,), (180, 180), tuple(range(1, 12))])
def test_demo_invalid_shift_inventory_is_not_published(shifts):
    with pytest.raises(ValueError):
        generated_case(shifts=shifts)


def test_demo_generator_does_not_authorize_infeasible_shift():
    context, inputs, document, baseline, _ = generated_case(shifts=(-600,))
    from a2_api.core.errors import DomainError

    with pytest.raises(DomainError):
        bridge.propose(context, inputs, document, baseline, profile_id=document.profile_id)


def _demo_snapshot_ids(value, run_ids=None):
    if isinstance(value, dict):
        return {
            key: "demo-moscow-TEST_ONLY"
            if key == "snapshot_id"
            else _demo_snapshot_ids(item, run_ids)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_demo_snapshot_ids(item, run_ids) for item in value]
    if isinstance(value, str) and run_ids:
        return run_ids.get(value, value)
    return value


def test_demo_saved_nonranking_policy_is_exact_on_both_scenario_sides():
    context, inputs, source, baseline, frozen = make_case()
    run_ids = {
        run.run_id: replace(run, snapshot_id="demo-moscow-TEST_ONLY").run_id
        for run in inputs.timetable.runs
    }
    context = V2JobContext.model_validate(_demo_snapshot_ids(context.model_dump(mode="json")))
    inputs = UrbanInputs.model_validate(_demo_snapshot_ids(inputs.model_dump(mode="json"), run_ids))
    payload = _demo_snapshot_ids(baseline.model_dump(mode="json"))
    payload["limitations"] = sorted(set(payload["limitations"]) | DEMO_LIMITATIONS)
    for score in payload["scores"]:
        score["ranking_eligible"] = False
    baseline = AnalysisResult.model_validate(payload)
    baseline.result_hash = canonical_hash(baseline.model_dump(mode="json", exclude={"result_hash"}))
    table = source.inputs.timetables[0]
    operation = source.plans[0].operation
    document = build_demo_scenarios(
        context,
        inputs,
        profile_id=source.profile_id,
        incoming_run_id=run_ids[operation.incoming_execution_id],
        outgoing_run_id=run_ids[operation.execution_id],
        shift_seconds=(180,),
        transport_modes=table.transport_modes,
        physical_services=tuple(
            replace(row, run_ids=tuple(run_ids[r] for r in row.run_ids))
            for row in table.physical_services
        ),
        evidence_refs=operation.evidence_refs,
    )
    immutable = baseline.model_dump_json()
    claim, *_ = with_claim((context, inputs, document, baseline, frozen))
    result = bridge.calculate(
        claim, baseline=baseline, inputs=inputs, document=document, profile_id=document.profile_id
    )
    assert result.before.scores == baseline.scores
    assert all(
        not score.ranking_eligible for score in [*result.before.scores, *result.after.scores]
    )
    assert DEMO_LIMITATIONS <= set(result.limitations)
    assert DEMO_LIMITATIONS <= set(result.assumptions)
    assert baseline.model_dump_json() == immutable
    # A prefix without the immutable declaration cannot weaken before equivalence.
    unlabelled = baseline.model_copy(update={"limitations": []})
    unlabelled.result_hash = canonical_hash(
        unlabelled.model_dump(mode="json", exclude={"result_hash"})
    )
    from a2_api.core.errors import DomainError

    with pytest.raises(DomainError, match="SCENARIO_RECOMPUTED_BASELINE_DIFFERS"):
        bridge.propose(context, inputs, document, unlabelled, profile_id=document.profile_id)
