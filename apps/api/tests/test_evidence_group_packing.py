"""TEST_ONLY lossless worker packing and exact saved-baseline publication fences."""

import json
import os
import sys
from types import SimpleNamespace

import pytest
from a2_api.adapters.evidence_groups import (
    pack_analysis_evidence,
    pack_proposal_evidence,
    pack_scenario_evidence,
)
from a2_api.core.errors import DomainError
from a2_api.core.jobs import CalculationOutput
from a2_api.workers import calculation
from a2_contracts.common import canonical_hash
from a2_contracts.v2.evidence import EvidenceGroup, EvidenceResolver
from a2_contracts.v2.results import AnalysisResult
from a2_contracts.v2.scenarios import ScenarioProposal
from pydantic import ValidationError
from test_product_scenario_publication import queued_case
from test_product_scenarios import make_case, run, with_claim
from test_v2_storage import proposal
from test_walk_observed import setup
from test_worker_transport import Capture

REF_FIELDS = {
    "source_refs",
    "evidence_refs",
    "calibration_evidence",
    "demand_evidence",
    "resource_evidence",
    "turnaround_evidence",
}


def leaf_facts(value):
    """Independent recursive comparison of all facts and expanded reference sets."""
    payload = value.model_dump(mode="json")
    resolver = EvidenceResolver(
        (row.evidence_id for row in value.evidence_refs), value.evidence_groups
    )

    def visit(current):
        if isinstance(current, list):
            return [visit(child) for child in current]
        if not isinstance(current, dict):
            return current
        return {
            key: sorted(resolver.expand(child))
            if key in REF_FIELDS
            and isinstance(child, list)
            and all(isinstance(ref, str) for ref in child)
            else visit(child)
            for key, child in current.items()
            if key not in {"evidence_groups", "result_hash"}
        }

    return visit(payload)


def large_analysis():
    out, _ = setup()
    result = out.finish()
    payload = result.model_dump(mode="json")
    leaves = ["TEST_ONLY-common-source-" + str(index).zfill(3) for index in range(96)]
    payload["evidence_refs"].extend(
        payload["evidence_refs"][0] | {"evidence_id": identity}
        for identity in [*leaves, "TEST_ONLY-unused-source"]
    )

    def add(current):
        if isinstance(current, list):
            for child in current:
                add(child)
        elif isinstance(current, dict):
            for key, child in current.items():
                if (
                    key in REF_FIELDS
                    and isinstance(child, list)
                    and child
                    and all(isinstance(ref, str) for ref in child)
                ):
                    current[key] = list(dict.fromkeys([*child, *leaves]))
                elif key != "evidence_refs":
                    add(child)

    add(payload)
    # A short edge referring to only part of a catalogue must stay narrow.
    payload["journeys"][0]["legs"][0]["evidence_refs"] = [leaves[0]]
    result = AnalysisResult.model_validate(payload)
    return result.model_copy(
        update={
            "result_hash": canonical_hash(result.model_dump(mode="json", exclude={"result_hash"}))
        }
    )


def test_packing_keeps_every_leaf_definition_fact_geometry_and_expanded_edge():
    original = large_analysis()
    original_bytes = original.model_dump_json()
    packed = pack_analysis_evidence(original)
    assert packed.evidence_groups
    assert packed.evidence_refs == original.evidence_refs
    assert leaf_facts(packed) == leaf_facts(original)
    assert original.model_dump_json() == original_bytes
    assert len(packed.model_dump_json()) < len(original_bytes)
    assert packed.journeys[0].legs[0].evidence_refs == original.journeys[0].legs[0].evidence_refs
    assert pack_analysis_evidence(packed) == packed
    assert packed.result_hash == canonical_hash(
        packed.model_dump(mode="json", exclude={"result_hash"})
    )
    assert AnalysisResult.model_validate_json(packed.model_dump_json()) == packed


def test_group_budget_does_not_discard_edges_or_facts(monkeypatch):
    from a2_api.adapters import evidence_groups

    original = large_analysis()
    monkeypatch.setattr(evidence_groups, "MAX_EVIDENCE_GROUPS", 0)
    assert pack_analysis_evidence(original) is original


def test_invalid_original_or_model_copy_group_is_rejected_before_packing():
    original = large_analysis()
    with pytest.raises(ValidationError, match="evidence"):
        pack_analysis_evidence(original.model_copy(update={"evidence_refs": []}))
    packed = pack_analysis_evidence(original)
    group = packed.evidence_groups[0].model_copy(update={"evidence_ids": ["TEST_ONLY-missing"]})
    with pytest.raises(ValidationError):
        pack_analysis_evidence(packed.model_copy(update={"evidence_groups": [group]}))


def test_command_packing_is_deterministic_bounded_and_keeps_fixed_operation():
    command = proposal(large_analysis())
    leaves = [row.evidence_id for row in command.evidence_refs]
    command = command.model_copy(
        update={
            "fixed_parameters": command.fixed_parameters.model_copy(
                update={
                    "demand_evidence": leaves,
                    "resource_evidence": leaves,
                    "turnaround_evidence": leaves,
                }
            )
        }
    )
    packed = pack_proposal_evidence(command)
    assert packed.evidence_groups
    assert leaf_facts(packed) == leaf_facts(command)
    assert packed.evidence_refs == command.evidence_refs
    assert packed.fixed_parameters.service_ids == command.fixed_parameters.service_ids
    assert packed.fixed_parameters.shift_seconds == command.fixed_parameters.shift_seconds
    assert pack_proposal_evidence(packed) == packed
    assert ScenarioProposal.model_validate_json(packed.model_dump_json()) == packed


def test_analysis_output_is_packed_before_actual_child_serialization(monkeypatch):
    analysis = pack_analysis_evidence(large_analysis())
    command = proposal(analysis).model_copy(update={"evidence_groups": analysis.evidence_groups})
    leaves = [row.evidence_id for row in command.evidence_refs]
    command = pack_proposal_evidence(
        command.model_copy(
            update={
                "fixed_parameters": command.fixed_parameters.model_copy(
                    update={
                        "demand_evidence": leaves,
                        "resource_evidence": leaves,
                        "turnaround_evidence": leaves,
                    }
                )
            }
        )
    )
    output = CalculationOutput(analysis=analysis, proposals=[command])
    monkeypatch.setitem(
        sys.modules, "TEST_ONLY_packed_handler", SimpleNamespace(run=lambda _: output)
    )
    claim = with_claim(make_case())[0]
    capture = Capture()
    calculation._child(
        capture, "TEST_ONLY_packed_handler:run", claim.model_dump_json(), os.getppid()
    )
    message = json.loads(capture.value)
    assert message["result_kind"] == "analysis_output"
    assert CalculationOutput.model_validate(message["result"]) == output
    assert message["result"]["analysis"]["evidence_groups"]
    assert message["result"]["proposals"][0]["evidence_groups"]
    assert len(capture.value) <= calculation.MAX_RESULT_BYTES == 16 * 1024 * 1024


def grouped_case():
    context, facts, document, baseline, frozen = make_case()
    payload = baseline.model_dump(mode="json")
    extra = payload["evidence_refs"][0] | {"evidence_id": "TEST_ONLY-extra-source"}
    payload["evidence_refs"].append(extra)
    members = sorted(row["evidence_id"] for row in payload["evidence_refs"])
    group = EvidenceGroup(
        group_id="evidence-group:" + canonical_hash(members), evidence_ids=members
    )
    payload["evidence_groups"] = [group.model_dump(mode="json")]
    for row in payload["metrics"]:
        if row["name"] != "urban_h_seconds":
            row["source_refs"] = [group.group_id]
    for row in payload["scores"]:
        row["evidence_refs"] = [group.group_id]
    baseline = AnalysisResult.model_validate(payload)
    baseline = baseline.model_copy(
        update={
            "result_hash": canonical_hash(baseline.model_dump(mode="json", exclude={"result_hash"}))
        }
    )
    return context, facts, document, baseline, frozen


def test_grouped_scenario_preserves_exact_before_h_and_reused_journeys():
    case = grouped_case()
    baseline = case[3]
    before_bytes = baseline.model_dump_json()
    result, _ = run(case)
    assert result.before.metrics == baseline.metrics
    assert result.before.scores == baseline.scores
    assert result.evidence_groups[: len(baseline.evidence_groups)] == baseline.evidence_groups
    assert baseline.model_dump_json() == before_bytes
    for previous in baseline.metrics:
        if previous.name == "urban_h_seconds":
            assert (
                next(row for row in result.after.metrics if row.metric_id == previous.metric_id)
                == previous
            )
    saved_journeys = {row.journey_id: row for row in baseline.journeys}
    for row in result.after_journeys:
        if row.journey_id in saved_journeys:
            assert row == saved_journeys[row.journey_id]
    assert pack_scenario_evidence(result, baseline) == result


@pytest.mark.asyncio
async def test_grouped_pg_roundtrip_cache_private_scope_and_exact_before_fence(database):
    pool, _, _ = database
    repository, claim, candidate, baseline = await queued_case(pool, grouped_case())
    baseline_bytes = baseline.model_dump_json()
    resolver = EvidenceResolver(
        (row.evidence_id for row in candidate.evidence_refs), candidate.evidence_groups
    )
    # An equivalent leaf representation is still forbidden in the saved before.
    metric = next(row for row in candidate.before.metrics if row.name != "urban_h_seconds")
    changed = metric.model_copy(update={"source_refs": sorted(resolver.expand(metric.source_refs))})
    altered_before = candidate.before.model_copy(
        update={
            "metrics": [
                changed if row.metric_id == metric.metric_id else row
                for row in candidate.before.metrics
            ]
        }
    )
    with pytest.raises(DomainError, match="before differs"):
        await repository.succeed(claim, candidate.model_copy(update={"before": altered_before}))
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE result_kind='scenario_v2'") == 0
    # Dropping an unused saved group or saved leaf is forbidden at publication.
    with pytest.raises((DomainError, ValidationError)):
        await repository.succeed(claim, candidate.model_copy(update={"evidence_groups": []}))
    with pytest.raises((DomainError, ValidationError)):
        await repository.succeed(
            claim,
            candidate.model_copy(
                update={
                    "evidence_refs": [
                        row
                        for row in candidate.evidence_refs
                        if row.evidence_id != "TEST_ONLY-extra-source"
                    ]
                }
            ),
        )
    await repository.succeed(claim, candidate)
    saved = await repository.get_run(candidate.run_id, claim.context.access_scope)
    assert saved.before == candidate.before and saved.after == candidate.after
    assert saved.evidence_groups == candidate.evidence_groups
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).model_dump_json() == baseline_bytes
    cached = await repository.enqueue(
        "scenario",
        claim.request,
        claim.context,
        "grouped-cache",
        "trace",
        scope=claim.context.access_scope,
    )
    assert cached.status == "succeeded"
    with pytest.raises(DomainError) as denied:
        await repository.get_run(saved.run_id, "TEST_ONLY-foreign-owner")
    assert denied.value.status_code == 404
