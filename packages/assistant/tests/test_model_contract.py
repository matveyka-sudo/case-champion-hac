"""One model answer format: a server clause over saved fact ids (BUG-001).

The example and every documented shape must pass the real Pydantic schema;
a wrong shape is schema_invalid and a wrong id is reference_invalid.
"""

import json
import logging

import pytest
from a2_assistant import FlashConfig
from a2_assistant.clauses import CLAUSE_IDS, _ModelSelection, suggested_clause
from a2_assistant.core import render_explanation
from a2_assistant.tools import AccessScope
from a2_contracts.v2.assistant import ExplanationRequest
from pydantic import ValidationError
from test_assistant import analysis, assistant, reply, score_analysis

SCORE = {"score_id": "accessibility"}
QUESTIONS = {
    "Почему такой индекс?": "explain",
    "Почему такая оценка?": "explain",
    "Объясни оценку доступности": "explain",
    "Какой сценарий можно проверить?": "improve",
    "Как улучшить сценарий?": "improve",
    "Какая цена поездки?": "unsupported",
    "Какие цены на билеты?": "unsupported",
    "Что сильнее влияет на результат?": "explain",
    "Какие источники использованы?": "sources",
    "На каком документе основан расчёт?": "sources",
    "Как считается индекс, какая формула?": "methodology",
    "Почему маршрут неизвестен?": "journey",
    "Что означает partial coverage?": "coverage",
    "Сравни два результата": "compare",
    "Почему задержка на границе?": "unsupported",
    "Что можно улучшить?": "improve",
    "Что известно о Москве по этому анализу?": "explain",
}


def scope(saved):
    return AccessScope(
        principal_id="owner",
        analysis_ids={saved.analysis_id},
        territory_ids={"city"},
        snapshot_ids={saved.snapshot_id},
        source_ids={e.source_id for e in saved.evidence_refs},
    )


async def ask(saved, content, question="Почему индекс такой?", raw=None):
    model_reply = reply(content)
    if raw is not None:
        model_reply.content = raw
    agent, transport, backend, budget = assistant(
        [model_reply],
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
        registered_template_ids={"coordinate-transfer"},
        request_id="contract",
        context=ExplanationRequest(question=question),
    )
    # A wrong answer never triggers a second model call to repair the format.
    assert len(transport.calls) == len(budget.reservations) == 1 and not backend.calls
    return result, transport


async def rejected(saved, content, caplog, category, **extra):
    with caplog.at_level(logging.WARNING, logger="a2_assistant"):
        result, _ = await ask(saved, content, **extra)
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert f"category={category}" in caplog.text
    assert render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("question,clause", QUESTIONS.items())
@pytest.mark.parametrize("fixture", ["score", "metric"])
async def test_every_prompt_example_is_a_complete_valid_answer(question, clause, fixture):
    saved = score_analysis(partial=True) if fixture == "score" else analysis()
    _, transport = await ask(saved, {"findings": [SCORE]}, question)
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    example = payload["response_example"]
    assert set(example) == {"clause_id", "findings", "evidence_ids", "template_ids"}
    assert example["clause_id"] == clause == suggested_clause(question)
    assert all(set(row) <= {"metric_id", "score_id"} for row in example["findings"])
    # The real schema, then the whole pipeline with the example as the model answer.
    _ModelSelection.model_validate(example)
    result, _ = await ask(saved, example, question)
    assert result.status == "generated" and result.reason_code is None
    assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_prompt_documents_exactly_the_schema_clause_ids():
    _, transport = await ask(score_analysis(partial=True), {"findings": [SCORE]})
    system = transport.calls[0]["messages"][0]["content"]
    listed = json.loads(system[system.rindex('{"clause_ids"') :])["clause_ids"]
    assert tuple(listed) == CLAUSE_IDS
    for clause in CLAUSE_IDS:
        assert f"{clause}=" in system
        _ModelSelection.model_validate({"clause_id": clause, "findings": [SCORE]})
    assert "answer_mode" not in system and "explanation_code" not in system


@pytest.mark.asyncio
@pytest.mark.parametrize("clause", [c for c in CLAUSE_IDS if c not in {"components", "time_scope"}])
async def test_full_valid_answer_is_generated_for_each_clause(clause):
    saved = score_analysis(partial=True)
    content = {
        "clause_id": clause,
        "findings": [SCORE],
        "evidence_ids": ["evidence"],
        "template_ids": [],
    }
    result, _ = await ask(saved, content)
    assert result.status == "generated"
    assert result.findings[0].score_claims[0].value == saved.scores[0].value
    assert result.findings[0].evidence_refs == ["evidence"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        {"clause_id": "explain", "findings": [SCORE | {"template_ids": []}]},
        {"clause_id": "explain", "findings": [SCORE | {"template_ids": ["coordinate-transfer"]}]},
        {"clause_id": "explain", "findings": [SCORE | {"paragraph": "Текст."}]},
        {"clause_id": "explain", "findings": [SCORE | {"evidence_ids": ["evidence"]}]},
        {"clause_id": "explain", "findings": [{"metric_id": None, "score_id": None}]},
        {"clause_id": "explain", "findings": [SCORE | {"metric_id": "travel-time"}]},
        {"clause_id": "explain", "findings": [{}]},
        {"clause_id": "explain", "findings": []},
        {"clause_id": "explain"},
        {"findings": [SCORE], "clause_id": "why"},
        {"findings": [SCORE], "answer_mode": "components"},
        {"findings": [SCORE | {"explanation_code": "partial"}]},
        {
            "clause_id": "explain",
            "findings": [SCORE],
            "paragraph": "Сохранено [[score:accessibility]].",
        },
        {"clause_id": "coverage", "answer_mode": "explain", "findings": [SCORE]},
        {"clause_id": "explain", "findings": [SCORE], "claims": [{"value": 1}]},
        {"clause_id": "explain", "findings": [SCORE], "template_ids": "coordinate-transfer"},
    ],
    ids=[
        "template_ids_in_finding",
        "filled_template_ids_in_finding",
        "paragraph_in_finding",
        "evidence_ids_in_finding",
        "null_ids",
        "both_ids",
        "empty_finding",
        "no_findings",
        "missing_findings",
        "clause_enum",
        "answer_mode_enum",
        "code_enum",
        "clause_with_paragraph",
        "clause_and_mode_disagree",
        "extra_top_level_key",
        "template_ids_not_a_list",
    ],
)
async def test_wrong_shape_is_a_schema_error_with_safe_fallback(content, caplog):
    with pytest.raises(ValidationError):
        _ModelSelection.model_validate(content)
    await rejected(score_analysis(partial=True), content, caplog, "schema_invalid")
    assert "category=reference_invalid" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        {"findings": [{"metric_id": "foreign-metric"}]},
        {"findings": [{"score_id": "foreign-score"}]},
        {"findings": [SCORE], "evidence_ids": ["foreign-evidence"]},
        {"findings": [SCORE, SCORE]},
        {"findings": [SCORE], "evidence_ids": ["evidence", "evidence"]},
    ],
    ids=["metric", "score", "evidence", "repeated_fact", "repeated_evidence"],
)
async def test_valid_shape_with_foreign_or_repeated_ids_is_a_reference_error(content, caplog):
    content = {"clause_id": "explain"} | content
    _ModelSelection.model_validate(content)
    await rejected(score_analysis(partial=True), content, caplog, "reference_invalid")
    assert "category=schema_invalid" not in caplog.text


@pytest.mark.asyncio
async def test_unregistered_template_is_rejected_at_top_level(caplog):
    content = {"clause_id": "improve", "findings": [SCORE], "template_ids": ["foreign"]}
    await rejected(score_analysis(partial=True), content, caplog, "proposal_invalid")
    ok, _ = await ask(
        score_analysis(partial=True),
        {"clause_id": "improve", "findings": [SCORE], "template_ids": ["coordinate-transfer"]},
    )
    assert ok.status == "generated" and ok.proposals[0].template_id == "coordinate-transfer"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [25.0, None])
async def test_status_code_is_derived_and_a_wrong_supplied_code_is_still_rejected(value, caplog):
    saved = score_analysis(value=value, partial=True)
    result, _ = await ask(saved, {"clause_id": "coverage", "findings": [SCORE]})
    assert result.status == "generated"
    assert result.findings[0].score_claims[0].value == value
    wrong = "unknown" if value is not None else "saved_value"
    content = {"clause_id": "coverage", "findings": [SCORE | {"explanation_code": wrong}]}
    await rejected(saved, content, caplog, "narrative_invalid")


@pytest.mark.asyncio
async def test_markdown_fence_and_text_outside_json(caplog):
    saved = score_analysis(partial=True)
    body = json.dumps({"clause_id": "explain", "findings": [SCORE]})
    fenced, _ = await ask(saved, None, raw="```json\n" + body + "\n```")
    assert fenced.status == "generated"
    await rejected(saved, None, caplog, "json_invalid", raw="Ответ: " + body)


@pytest.mark.asyncio
async def test_explicit_null_for_the_unused_id_is_tolerated():
    content = {"clause_id": "explain", "findings": [SCORE | {"metric_id": None}]}
    result, _ = await ask(score_analysis(partial=True), content)
    assert result.status == "generated"
