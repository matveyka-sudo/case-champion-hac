"""TEST_ONLY lossless group closure, mutation rejection and old wire identity."""

import hashlib
import json
from pathlib import Path

import pytest
from a2_contracts.common import canonical_hash
from a2_contracts.v2.evidence import EvidenceGroup, EvidenceResolver, require_result_evidence
from a2_contracts.v2.results import AnalysisResult, Metric, Score, validate_score_dependencies
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioResult
from pydantic import ValidationError, create_model
from test_v2_scenarios import proposal, result

ROOT = Path(__file__).parents[1]


def group(*leaves):
    leaves = sorted(set(leaves))
    return EvidenceGroup(group_id="evidence-group:" + canonical_hash(leaves), evidence_ids=leaves)


def test_old_no_group_wire_and_hashes_match_separate_pre_group_source_baseline():
    baseline = json.loads((ROOT / "tests/fixtures/evidence-groups-legacy-wire.json").read_text())[
        "models"
    ]
    models = {
        path.stem: AnalysisResult.model_validate(json.loads(path.read_bytes())["payload"])
        for path in (ROOT / "fixtures/v2").glob("*-result.json")
    }
    models.update(
        proposal=ScenarioProposal.model_validate(proposal()),
        scenario=ScenarioResult.model_validate(result()),
    )
    holder = create_model("Holder", proposal=(ScenarioProposal, ...))
    models["nested_proposal"] = holder(proposal=models["proposal"])
    for name, model in models.items():
        assert "evidence_groups" not in model.model_dump_json()
        assert (
            hashlib.sha256(model.model_dump_json().encode()).hexdigest()
            == baseline[name]["json_sha256"]
        )
        assert canonical_hash(model.model_dump(mode="json")) == baseline[name]["canonical_hash"]
        assert getattr(model, "result_hash", None) == baseline[name]["result_hash"]
        if name != "nested_proposal":
            explicit = type(model).model_validate(
                model.model_dump(mode="json") | {"evidence_groups": []}
            )
            assert explicit.model_dump_json() == model.model_dump_json()


def test_mixed_references_expand_once_to_exact_existing_leaves():
    first = group("leaf-A", "leaf-B")
    resolver = EvidenceResolver(["leaf-A", "leaf-B", "leaf-C"], [first])
    assert resolver.expand([first.group_id, "leaf-A", "leaf-C"]) == {"leaf-A", "leaf-B", "leaf-C"}
    assert resolver.require([first.group_id, "leaf-C"]) is None
    with pytest.raises(ValueError, match="Unknown evidence"):
        resolver.expand(["FOREIGN"])
    with pytest.raises(ValueError):
        resolver.expand(first.group_id)


def test_absence_dependency_compares_expanded_leaves_instead_of_group_spelling():
    from test_scenario_effect_coverage import score

    shared = group("leaf-A", "leaf-B")
    resolver = EvidenceResolver(["leaf-A", "leaf-B"], [shared])
    absent = Score.model_validate(
        score("absent", 0.0)
        | {
            "reason_code": "NO_FEASIBLE_ROUTE",
            "calculation_kind": "aggregate",
            "evidence_refs": ["leaf-A", "leaf-B"],
        }
    )
    metric = Metric(
        metric_id="absence-proof",
        name="minimum_journey_seconds",
        value=None,
        unit="seconds",
        status="not_applicable",
        reason_code="NO_FEASIBLE_ROUTE",
        source_refs=[shared.group_id],
        subject=absent.subject,
        completeness="full",
        time_kind="complete_chain",
    )
    validate_score_dependencies([metric], [absent], evidence_resolver=resolver)
    with pytest.raises(ValueError, match="owning resolver"):
        validate_score_dependencies([metric], [absent])
    validate_score_dependencies([metric], [absent], allow_deferred_groups=True)
    absent.evidence_refs = ["leaf-A"]
    with pytest.raises(ValueError, match="placeholder"):
        validate_score_dependencies([metric], [absent], evidence_resolver=resolver)


@pytest.mark.parametrize(
    "attack",
    [
        "hash",
        "duplicate_member",
        "order",
        "nested",
        "missing",
        "duplicate_group",
        "collision",
        "mutated",
        "group_count",
        "member_count",
        "total_members",
    ],
)
def test_groups_fail_closed_for_invalid_or_mutated_ownership(attack):
    first = group("leaf-A", "leaf-B")
    leaves, groups = ["leaf-A", "leaf-B"], [first]
    with pytest.raises((ValueError, ValidationError)):
        if attack in {"hash", "duplicate_member", "order", "nested", "member_count"}:
            payload = first.model_dump()
            if attack == "hash":
                payload["group_id"] = "evidence-group:" + "0" * 64
            elif attack == "duplicate_member":
                payload["evidence_ids"].append("leaf-A")
            elif attack == "order":
                payload["evidence_ids"].reverse()
            elif attack == "nested":
                payload["evidence_ids"] = ["leaf-A", first.group_id]
            else:
                payload["evidence_ids"] = [f"leaf-{n}" for n in range(4097)]
            EvidenceGroup.model_validate(payload)
        else:
            if attack == "missing":
                leaves.pop()
            elif attack == "duplicate_group":
                groups.append(first)
            elif attack == "collision":
                leaves.append(first.group_id)
            elif attack == "mutated":
                first.evidence_ids.append("FOREIGN")
            elif attack == "group_count":
                groups *= 8193
            else:
                leaves = [f"leaf-{n:05}" for n in range(4113)]
                groups = [group(*leaves[n : n + 4096]) for n in range(17)]
            EvidenceResolver(leaves, groups)


@pytest.mark.parametrize("kind", ["analysis", "scenario", "proposal"])
def test_each_top_level_owner_accepts_groups_and_rechecks_mutated_edges(kind):
    if kind == "analysis":
        raw = json.loads((ROOT / "fixtures/v2/urban_passenger-result.json").read_bytes())["payload"]
        model = AnalysisResult
        refs = raw["metrics"][0]["source_refs"]
    elif kind == "scenario":
        raw, model = result(), ScenarioResult
        refs = raw["after"]["metrics"][0]["source_refs"]
    else:
        raw, model = proposal(), ScenarioProposal
        refs = raw["fixed_parameters"]["resource_evidence"]
    original = raw["evidence_refs"][0]
    extra = original | {"evidence_id": "TEST_ONLY-extra"}
    raw["evidence_refs"].append(extra)
    shared = group(original["evidence_id"], extra["evidence_id"])
    raw["evidence_groups"] = [shared.model_dump()]
    refs[:] = [shared.group_id, original["evidence_id"]]
    parsed = model.model_validate(raw)
    resolver = require_result_evidence(parsed)
    assert resolver.expand(refs) == set(shared.evidence_ids)
    assert type(parsed).model_validate_json(parsed.model_dump_json()) == parsed
    parsed.evidence_groups[0].evidence_ids.append("FOREIGN")
    with pytest.raises(ValueError):
        require_result_evidence(parsed)
