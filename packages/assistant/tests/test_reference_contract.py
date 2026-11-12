"""Model references against the grounding contract (live reference_invalid, 11.11).

Live-like config: no tools, one model call, no retries. Fake transport only.
"""

import json
import logging

import pytest
from a2_assistant import FlashConfig
from a2_assistant.core import render_explanation
from a2_assistant.tools import AccessScope
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, assistant, reply, score_analysis
from test_assistant_evidence_closure import grouped_saved
from test_context_v2 import linked_result

SCORE = {"score_id": "accessibility", "explanation_code": "saved_value"}
FOREIGN = "DO_NOT_LOG_foreign"


def scope(saved):
    return AccessScope(
        principal_id="owner",
        analysis_ids={saved.analysis_id},
        territory_ids={"city"},
        snapshot_ids={saved.snapshot_id},
        source_ids={e.source_id for e in saved.evidence_refs},
    )


async def run(saved, content, question="Почему индекс такой?"):
    agent, transport, backend, budget = assistant(
        [reply(content)],
        config=FlashConfig(
            api_key="TEST_ONLY",
            folder_id="TEST_ONLY",
            tools_enabled=False,
            max_model_calls=1,
            max_retries=0,
        ),
    )
    result = await agent.explain(
        saved,
        access_scope=scope(saved),
        registered_template_ids=set(),
        request_id="references",
        context=ExplanationRequest(question=question),
    )
    assert len(transport.calls) == len(budget.reservations) == 1 and not backend.calls
    return result, json.loads(transport.calls[0]["messages"][1]["content"])


async def rejected(saved, content, caplog, category, question="Почему индекс такой?"):
    with caplog.at_level(logging.WARNING, logger="a2_assistant"):
        result, _ = await run(saved, content, question)
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert f"category={category}" in caplog.text
    assert FOREIGN not in caplog.text
    # The fallback still explains the saved facts, never the model answer.
    assert render_explanation(result, saved) and result.model_id is None


def two_metrics():
    raw = analysis().model_dump(mode="json")
    raw["metrics"].append(
        raw["metrics"][0] | {"metric_id": "other-time", "source_refs": ["other-evidence"]}
    )
    raw["evidence_refs"].append(raw["evidence_refs"][0] | {"evidence_id": "other-evidence"})
    return AnalysisResult.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", [score_analysis, analysis])
async def test_live_shape_example_without_paragraph_is_generated(fixture):
    """The 11.11 shape: the example's answer_mode and evidence_ids, paragraph dropped."""
    saved = fixture(partial=True) if fixture is score_analysis else fixture()
    _, payload = await run(saved, {"findings": [SCORE], "template_ids": []})
    example = payload["response_example"]
    assert example["evidence_ids"]
    shape = {key: value for key, value in example.items() if key != "paragraph"}
    result, _ = await run(saved, shape)
    assert result.status == "generated"
    assert result.findings[0].evidence_refs == example["evidence_ids"]
    assert render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    [{"answer_mode": "explain"}, {"clause_id": "explain"}, {"clause_id": "coverage"}, {}],
    ids=["answer_mode", "clause", "clause_coverage", "legacy"],
)
@pytest.mark.parametrize("evidence_ids", [["evidence"], []], ids=["copied", "empty"])
async def test_allowed_copied_references_are_accepted(shape, evidence_ids):
    saved = score_analysis(partial=True)
    result, _ = await run(
        saved, {"findings": [SCORE], "template_ids": [], "evidence_ids": evidence_ids, **shape}
    )
    assert result.status == "generated"
    # The server cites the selected fact closure whatever the model copied.
    assert result.findings[0].evidence_refs == ["evidence"]
    assert result.findings[0].score_claims[0].value == saved.scores[0].value


@pytest.mark.asyncio
async def test_a_partial_copy_of_the_closure_keeps_server_citations():
    saved, leaves = grouped_saved()
    finding = {"metric_id": "travel-time", "explanation_code": "travel_time"}
    result, _ = await run(
        saved,
        {"answer_mode": "explain", "findings": [finding], "evidence_ids": leaves[:1]},
        "Как читать этот сохранённый показатель?",
    )
    assert result.status == "generated" and result.findings[0].evidence_refs == leaves


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"findings": [{"score_id": FOREIGN, "explanation_code": "saved_value"}]},
        {"findings": [{"score_id": "[[score:accessibility]]", "explanation_code": "saved_value"}]},
        {"findings": [{"score_id": "evidence", "explanation_code": "saved_value"}]},
        {"findings": [{"metric_id": "accessibility", "explanation_code": "saved_value"}]},
        {"findings": [SCORE, SCORE]},
        {"evidence_ids": [FOREIGN]},
        {"evidence_ids": ["accessibility"]},
        {"evidence_ids": ["[[score:accessibility]]"]},
        {"evidence_ids": ["evidence", "evidence"]},
        {"evidence_ids": ["evidence", "https://example.org"]},
    ],
    ids=[
        "unknown_score",
        "placeholder_as_score",
        "evidence_as_score",
        "score_as_metric",
        "duplicate_finding",
        "unknown_evidence",
        "score_as_evidence",
        "placeholder_as_evidence",
        "duplicate_evidence",
        "url_as_evidence",
    ],
)
@pytest.mark.parametrize("shape", [{"answer_mode": "explain"}, {"clause_id": "explain"}, {}])
async def test_invented_or_wrong_kind_references_stay_blocked(change, shape, caplog):
    content = {"findings": [SCORE], "template_ids": [], **shape} | change
    await rejected(score_analysis(partial=True), content, caplog, "reference_invalid")


@pytest.mark.asyncio
async def test_a_saved_fact_outside_the_compact_context_is_rejected(caplog):
    saved = linked_result()
    _, payload = await run(saved, {"findings": [SCORE]}, "Почему такой индекс?")
    sent = {row["metric_id"] for row in payload["saved_facts"]["metrics"]}
    outside = next(m.metric_id for m in reversed(saved.metrics) if m.metric_id not in sent)
    finding = {"metric_id": outside, "explanation_code": "saved_value"}
    content = {"answer_mode": "explain", "findings": [finding]}
    await rejected(saved, content, caplog, "reference_invalid", "Почему такой индекс?")


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", [{"answer_mode": "explain"}, {"clause_id": "explain"}, {}])
async def test_evidence_of_an_unselected_fact_is_rejected(shape, caplog):
    saved = two_metrics()
    finding = {"metric_id": "travel-time", "explanation_code": "travel_time"}
    content = {"findings": [finding], "evidence_ids": ["other-evidence"], **shape}
    await rejected(saved, content, caplog, "reference_invalid", "Сколько ехать?")


@pytest.mark.asyncio
async def test_empty_findings_are_a_schema_error(caplog):
    content = {"answer_mode": "explain", "findings": [], "evidence_ids": ["evidence"]}
    await rejected(score_analysis(partial=True), content, caplog, "schema_invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("clause", ["components", "time_scope"])
async def test_unmet_clause_is_not_reported_as_a_bad_reference(clause, caplog):
    content = {"clause_id": clause, "findings": [SCORE]}
    await rejected(score_analysis(partial=True), content, caplog, "clause_invalid")
    assert "category=reference_invalid" not in caplog.text


@pytest.mark.asyncio
async def test_prompt_states_reference_and_clause_rules():
    _, payload = await run(score_analysis(partial=True), {"findings": [SCORE]})
    agent, transport, *_ = assistant(
        [reply({"findings": [SCORE]})],
        config=FlashConfig(api_key="TEST_ONLY", folder_id="TEST_ONLY", tools_enabled=False),
    )
    await agent.explain(
        score_analysis(partial=True),
        access_scope=scope(score_analysis(partial=True)),
        registered_template_ids=set(),
        request_id="prompt",
        context=ExplanationRequest(question="Почему индекс такой?"),
    )
    system = transport.calls[0]["messages"][0]["content"]
    assert "saved metric_id OR score_id (no placeholder)" in system
    assert "components: score + its component metric" in system
    assert payload["response_example"]["findings"] == [SCORE]


def two_linked_scores():
    raw = score_analysis(partial=True).model_dump(mode="json")
    metric = analysis().metrics[0].model_dump(mode="json")
    raw["metrics"] = [metric | {"metric_id": "m0"}, metric | {"metric_id": "m1"}]
    first = raw["scores"][0] | {"score_id": "first"}
    first["components"] = [{"kind": "metric", "id": "m0"}, {"kind": "metric", "id": "m1"}]
    last = raw["scores"][0] | {"score_id": "last"}
    last["components"] = [{"kind": "metric", "id": "m0"}]
    raw["scores"] = [first, last]
    return AnalysisResult.model_validate(raw)


@pytest.mark.asyncio
async def test_components_truncation_is_marked_per_score():
    saved = two_linked_scores()
    finding = {"score_id": "first", "explanation_code": "saved_value"}
    for limit in range(12000, 1024, -50):
        agent, transport, *_ = assistant(
            [reply({"answer_mode": "explain", "findings": [finding]})],
            config=FlashConfig(
                api_key="TEST_ONLY",
                folder_id="TEST_ONLY",
                tools_enabled=False,
                max_input_tokens=limit,
            ),
        )
        await agent.explain(
            saved,
            access_scope=scope(saved),
            registered_template_ids=set(),
            request_id="components",
            context=ExplanationRequest(question="Почему такой индекс?"),
        )
        if not transport.calls:
            break
        facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
        sent = {row["metric_id"] for row in facts["metrics"]}
        rows = {row["score_id"]: row for row in facts["scores"]}
        if "m1" not in sent and "first" in rows:
            assert rows["first"]["components"] == [{"kind": "metric", "id": "m0"}]
            assert rows["first"]["components_truncated"] is True
            return
    pytest.fail("no input limit dropped only the second component")
