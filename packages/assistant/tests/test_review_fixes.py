"""Regressions for the 11.11 assistant review: unsupported prose, limitations, citations, focus."""

import json

import pytest
from a2_assistant.core import render_explanation
from a2_assistant.focus import focused_scores
from a2_assistant.tools import AccessScope
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import Backend, assistant, reply, retrieved_hit, score_analysis, tool
from test_freeform import answer
from test_grounded_prose import prose_reply


def with_limitations(*texts):
    raw = score_analysis(partial=True).model_dump(mode="json")
    raw["limitations"] = list(texts)
    return AnalysisResult.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question", ["Почему индекс такой?", "Почему разрушено дорожное покрытие?"]
)
@pytest.mark.parametrize(
    "sentence", ["Дорожное покрытие разрушено.", "Покрытие дорог не отремонтировано."]
)
async def test_a_condition_absent_from_saved_data_is_not_generated(question, sentence):
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        question,
        "explain",
        responses=[prose_reply("Сохранено [[score:accessibility]]. " + sentence)],
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sentence",
    [
        "Сохранённые данные не описывают дорожное покрытие.",
        "По этим данным нельзя установить состояние дорог.",
        "Сведений о ремонте дорог нет данных в расчёте.",
    ],
)
async def test_the_limit_of_saved_data_can_be_stated_in_plain_words(sentence):
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        "Почему разрушено дорожное покрытие?",
        "explain",
        responses=[prose_reply("Сохранено [[score:accessibility]]. " + sentence)],
    )
    assert result.status == "generated"
    assert sentence in render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "coverage"])
async def test_generated_answer_keeps_the_saved_limitations(mode):
    saved = with_limitations("Каталог школ неполный.", "Расписание модельное.")
    responses = [prose_reply("Сохранено [[score:accessibility]].")] if mode is None else None
    result, *_ = await answer(
        saved, "Что ограничивает этот индекс?", mode or "coverage", responses=responses
    )
    assert result.status == "generated"
    assert result.limitations[:2] == ["Каталог школ неполный.", "Расписание модельное."]
    assert "Объяснение ограничено сохранёнными данными." in result.limitations
    assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_saved_limitations_and_request_reach_the_model_context():
    saved = with_limitations("Каталог школ неполный.")
    _, transport, *_ = await answer(saved, "Что ограничивает этот индекс?", "coverage")
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert facts["limitations"] == ["Каталог школ неполный."]
    assert facts["request"]["analysis_kind"] == saved.analysis_kind
    assert "methodology" not in facts["request"] and "schema_version" not in facts["request"]


@pytest.mark.asyncio
async def test_a_retrieved_document_can_ground_the_paragraph():
    saved = score_analysis(partial=True)
    backend = Backend(
        dict(
            status="available",
            completeness="partial",
            reason_code=None,
            hits=[retrieved_hit()],
            truncated=False,
        )
    )
    agent, *_ = assistant(
        [
            prose_reply(
                "Сохранено [[score:accessibility]].",
                evidence_ids=["evidence", "retrieved"],
            )
        ],
        backend=backend,
    )
    agent.transport.responses.insert(
        0,
        reply(
            calls=[tool("search_evidence", {"query": "пересадка", "territory_ids": ["city"]})],
            finish_reason="tool_calls",
        ),
    )
    scope = AccessScope(
        principal_id="owner",
        analysis_ids={saved.analysis_id},
        territory_ids={"city"},
        snapshot_ids={saved.snapshot_id},
        source_ids={e.source_id for e in saved.evidence_refs} | {"source"},
    )
    result = await agent.explain(
        saved,
        access_scope=scope,
        registered_template_ids=set(),
        request_id="retrieved",
        context=ExplanationRequest(question="Есть ли документ о пересадке?"),
    )
    assert result.status == "generated"
    assert result.findings[0].evidence_refs == ["evidence", "retrieved"]
    assert [e.evidence_id for e in result.retrieved_evidence] == ["retrieved"]
    assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_an_unretrieved_document_still_cannot_ground_the_paragraph():
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        "Есть ли документ?",
        "explain",
        responses=[prose_reply("Сохранено [[score:accessibility]].", evidence_ids=["retrieved"])],
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"


def test_facts_that_match_the_question_are_selected_first():
    raw = score_analysis().model_dump(mode="json")
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "airport",
            "name": "Индекс категории",
            "subject": {"kind": "sample_point", "point_id": "point", "category": "airport"},
        }
    )
    saved = AnalysisResult.model_validate(raw)
    plain = [s.score_id for s in focused_scores(saved)]
    asked = focused_scores(saved, ExplanationRequest(question="Сколько ехать до аэропорта?"))
    assert plain == ["accessibility", "airport"]
    assert [s.score_id for s in asked] == ["airport", "accessibility"]
