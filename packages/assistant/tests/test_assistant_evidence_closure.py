"""TEST_ONLY: grouped saved provenance stays leaf-grounded and local."""

import json

import pytest
from a2_assistant.core import render_explanation, template_explanation
from a2_contracts.common import canonical_hash
from a2_contracts.v2.assistant import Explanation, ExplanationRequest
from a2_contracts.v2.evidence import EvidenceGroup
from a2_contracts.v2.results import AnalysisResult
from test_assistant import (
    Backend,
    analysis,
    assistant,
    reply,
    retrieved_hit,
    scope,
    score_analysis,
    tool,
)


def group(leaves):
    leaves = sorted(set(leaves))
    return EvidenceGroup(group_id="evidence-group:" + canonical_hash(leaves), evidence_ids=leaves)


def test_repeated_group_citations_expand_once_and_keep_legacy_leaf_order(monkeypatch):
    from a2_assistant.evidence import leaf_references
    from a2_contracts.v2.evidence import EvidenceResolver

    leaves = [f"leaf-{index:04}" for index in range(4096)]
    grouped = group(leaves)
    resolver = EvidenceResolver([*leaves, "outside"], [grouped])
    original_expand = resolver.expand
    calls = []

    def counted(refs):
        calls.append(tuple(refs))
        return original_expand(calls[-1])

    monkeypatch.setattr(resolver, "expand", counted)
    refs = ["outside", *([grouped.group_id] * 4000), leaves[0]]
    assert leaf_references(refs, resolver) == ["outside", *leaves]
    assert calls == [(grouped.group_id,)]
    assert leaf_references(["outside", leaves[0], "outside"], resolver) == [
        "outside",
        leaves[0],
        "outside",
    ]
    assert original_expand([grouped.group_id] * 4000) == frozenset(leaves)


def grouped_saved(kind="metric", members=2, *, value=25.0):
    raw = (
        analysis() if kind == "metric" else score_analysis(value=value, partial=True)
    ).model_dump(mode="json")
    prototype = raw["evidence_refs"][0]
    leaves = ["evidence", *(f"leaf-{index:04}" for index in range(1, members))]
    raw["evidence_refs"] = [prototype | {"evidence_id": leaf} for leaf in [*leaves, "unused-leaf"]]
    grouped = group(leaves)
    raw["evidence_groups"] = [grouped.model_dump(mode="json")]
    if kind == "metric":
        raw["metrics"][0]["source_refs"] = [grouped.group_id]
    else:
        raw["scores"][0]["evidence_refs"] = [grouped.group_id]
    return AnalysisResult.model_validate(raw), leaves


def selected(kind="metric", *, evidence_ids=None, paragraph=True):
    return {
        "findings": [
            {"metric_id": "travel-time", "explanation_code": "travel_time"}
            if kind == "metric"
            else {"score_id": "accessibility", "explanation_code": "saved_value"}
        ],
        **(
            {
                "paragraph": "Разберём сохранённый показатель: [["
                + ("metric:travel-time" if kind == "metric" else "score:accessibility")
                + "]]. [[coverage]]",
                "evidence_ids": ["evidence"] if evidence_ids is None else evidence_ids,
            }
            if paragraph
            else {}
        ),
    }


async def explain(agent, saved):
    return await agent.explain(
        saved,
        access_scope=scope(),
        registered_template_ids=set(),
        request_id="TEST_ONLY-groups",
        context=ExplanationRequest(question="Как читать этот сохранённый показатель?"),
    )


@pytest.mark.parametrize("value", [None, 0.0, 25.0])
def test_template_expands_score_group_without_changing_unknown_zero_or_partial(value):
    saved, leaves = grouped_saved("score", value=value)
    original = saved.model_dump_json()
    result = template_explanation(saved, "TEST_ONLY-request", "DISABLED")
    assert result.findings[0].evidence_refs == leaves
    assert result.evidence_refs == leaves
    assert result.findings[0].score_claims[0].value == value
    assert saved.scores[0].completeness == "partial"
    assert "полнота данных ограничена" in render_explanation(result, saved)
    assert saved.model_dump_json() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["metric", "score"])
@pytest.mark.parametrize("paragraph", [False, True])
async def test_facts_and_all_generated_paths_use_expanded_leaf_citations(kind, paragraph):
    saved, leaves = grouped_saved(kind)
    original = saved.model_dump_json()
    data = selected(kind, paragraph=paragraph)
    if not paragraph:
        data["answer_mode"] = "coverage"
    agent, transport, backend, budget = assistant([reply(data)])
    result = await explain(agent, saved)
    assert result.status == "generated"
    assert result.findings[0].evidence_refs == result.evidence_refs == leaves
    assert render_explanation(result, saved)
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    facts = payload["saved_facts"]["metrics" if kind == "metric" else "scores"]
    assert facts[0]["evidence_ids"] == leaves
    assert payload["response_example"]["evidence_ids"] == leaves
    assert "evidence-group:" not in json.dumps(transport.calls)
    assert len(transport.calls) == len(budget.reservations) == 1
    assert not backend.calls and saved.model_dump_json() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("citation", ["group", "unused-leaf", "foreign", "https://foreign.test/"])
async def test_model_cannot_cite_group_or_leaf_outside_selected_fact_closure(citation):
    saved, _ = grouped_saved()
    citation = saved.evidence_groups[0].group_id if citation == "group" else citation
    agent, transport, _, budget = assistant([reply(selected(evidence_ids=[citation]))])
    result = await explain(agent, saved)
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert len(transport.calls) == len(budget.reservations) == 1
    assert all(not ref.startswith("evidence-group:") for ref in result.evidence_refs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["missing_group", "hash", "foreign_leaf", "missing_leaf", "edge"]
)
async def test_mutated_saved_closure_is_rejected_before_model_or_budget(attack):
    saved, _ = grouped_saved()
    if attack == "missing_group":
        saved.evidence_groups.clear()
    elif attack == "hash":
        saved.evidence_groups[0].evidence_ids.append("unused-leaf")
    elif attack == "foreign_leaf":
        replacement = group(["evidence", "foreign"])
        saved.evidence_groups = [replacement]
        saved.metrics[0].source_refs = [replacement.group_id]
    elif attack == "missing_leaf":
        saved.evidence_refs.pop(1)
    else:
        saved.metrics[0].source_refs.append("foreign")
    agent, transport, backend, budget = assistant([reply(selected())])
    with pytest.raises(ValueError):
        await explain(agent, saved)
    with pytest.raises(ValueError):
        template_explanation(saved, "TEST_ONLY-request", "DISABLED")
    assert not transport.calls and not budget.reservations and not backend.calls


@pytest.mark.asyncio
async def test_local_prose_witness_binds_even_unreferenced_group_mapping_and_json_stays_untrusted():
    saved, _ = grouped_saved("score")
    agent, *_ = assistant([reply(selected("score"))])
    result = await explain(agent, saved)
    assert result.status == "generated"
    changed = saved.model_copy(deep=True)
    changed.evidence_groups.append(group(["evidence", "unused-leaf"]))
    assert changed.result_hash == saved.result_hash
    assert changed.scores == saved.scores and changed.evidence_refs == saved.evidence_refs
    with pytest.raises(ValueError, match="narrative_invalid"):
        render_explanation(result, changed)
    public = Explanation.model_validate_json(result.model_dump_json())
    with pytest.raises(ValueError, match="narrative_invalid"):
        render_explanation(public, saved)
    assert render_explanation(result.model_copy(deep=True), saved)


@pytest.mark.asyncio
async def test_large_group_keeps_existing_prompt_and_citation_caps_without_losing_saved_leaves():
    saved, leaves = grouped_saved(members=61)
    original = saved.model_dump_json()
    agent, transport, _, budget = assistant([reply(selected())])
    result = await explain(agent, saved)
    assert result.status == "generated"
    assert result.findings[0].evidence_refs == result.evidence_refs == leaves[:50]
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    assert payload["saved_facts"]["metrics"][0]["evidence_ids"] == leaves[:5]
    assert payload["response_example"]["evidence_ids"] == leaves[:5]
    assert transport.calls[0]["max_tokens"] == 2000 and len(budget.reservations) == 1
    assert len(saved.evidence_refs) == 62 and saved.model_dump_json() == original
    assert result.findings[0].claims[0].value == saved.metrics[0].value


def test_legacy_leaf_order_and_empty_group_serialization_are_unchanged():
    raw = analysis().model_dump(mode="json")
    raw["evidence_refs"].append(raw["evidence_refs"][0] | {"evidence_id": "earlier-leaf"})
    raw["metrics"][0]["source_refs"] = ["evidence", "earlier-leaf", "evidence"]
    legacy = AnalysisResult.model_validate(raw)
    empty = AnalysisResult.model_validate(raw | {"evidence_groups": []})
    assert legacy.model_dump_json() == empty.model_dump_json()
    assert "evidence_groups" not in legacy.model_dump(mode="json")
    first = template_explanation(legacy, "TEST_ONLY-request", "DISABLED")
    second = template_explanation(empty, "TEST_ONLY-request", "DISABLED")
    assert first.model_dump_json() == second.model_dump_json()
    assert first.evidence_refs == ["evidence", "earlier-leaf"]
    assert first.findings[0].evidence_refs == ["evidence", "earlier-leaf", "evidence"]


@pytest.mark.asyncio
async def test_retrieval_cannot_turn_group_identity_into_a_url_or_send_it_onward():
    saved, _ = grouped_saved()
    hit = retrieved_hit() | {"evidence_id": saved.evidence_groups[0].group_id}
    backend = Backend(
        dict(
            status="available",
            completeness="partial",
            reason_code=None,
            hits=[hit],
            truncated=False,
        )
    )
    agent, transport, _, budget = assistant(
        [
            reply(
                calls=[tool("search_evidence", {"query": "пересадка", "territory_ids": ["city"]})],
                finish_reason="tool_calls",
            ),
            reply(selected()),
        ],
        backend=backend,
    )
    result = await explain(agent, saved)
    assert result.reason_code == "TOOL_UNAVAILABLE" and result.retrieved_evidence == []
    assert len(transport.calls) == len(budget.reservations) == 1
