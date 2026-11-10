"""Explicit synthetic-demo plans use the existing production scenario bridge."""

import pytest
from a2_api.adapters import product_scenarios as bridge
from a2_api.adapters.demo_scenarios import build_demo_scenarios
from a2_api.adapters.product_scenario_inputs import ScenarioDocument
from a2_contracts.common import canonical_hash
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
