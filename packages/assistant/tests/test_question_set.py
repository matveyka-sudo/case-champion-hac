"""Offline question set: each answer is relevant, grounded and keeps its limits (stage 5, 6).

The model double answers with the server's own example, as live Flash does.
"""

import json
import re

import pytest
from a2_assistant.context.retrieval import needs_documents
from a2_assistant.core import render_explanation
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import Backend, analysis, assistant, reply, retrieved_hit, score_analysis
from test_context_v2 import linked_result

LIMIT = "SAVED_LIMITATION_PARTIAL_COVERAGE"


def limited(saved):
    raw = saved.model_dump(mode="json")
    raw["limitations"] = [LIMIT]
    return AnalysisResult.model_validate(raw)


def two_points():
    raw = score_analysis().model_dump(mode="json")
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "second",
            "value": 40.0,
            "subject": {"kind": "sample_point", "point_id": "other-point"},
        }
    )
    return AnalysisResult.model_validate(raw)


def movement():
    saved = analysis()
    saved.metrics[0].time_kind = "movement"
    return saved


async def ask(saved, question, *, selection=None, second=None, hits=()):
    saved = limited(saved)
    backend = Backend(
        dict(
            status="available",
            completeness="partial",
            reason_code=None,
            hits=list(hits),
            truncated=False,
        )
    )

    async def echo():
        payload = json.loads(transport.calls[-1]["messages"][1]["content"])
        example = payload["response_example"]
        if second is not None and second not in example["findings"]:
            example = example | {"findings": [*example["findings"], second]}
        return reply(example)

    agent, transport, _, budget = assistant(
        [echo],
        backend=backend,
        config=FlashConfig(
            api_key="TEST_ONLY",
            folder_id="TEST_ONLY",
            document_retrieval=True,
            max_model_calls=1,
            max_retries=0,
        ),
    )
    result = await agent.explain(
        saved,
        access_scope=AccessScope(
            principal_id="owner",
            analysis_ids={saved.analysis_id},
            territory_ids={"city"},
            snapshot_ids={saved.snapshot_id},
            source_ids={"source"},
        ),
        registered_template_ids=set(),
        request_id="questions",
        context=ExplanationRequest(question=question, selection=selection),
    )
    assert len(transport.calls) == len(budget.reservations) == 1
    assert result.status == "generated", result.reason_code
    text = result.findings[0].text
    # Grounding: every number is a saved claim; the text itself carries no digits
    # except provenance dates of the sources clause.
    for claim in result.findings[0].score_claims:
        assert claim.value == next(s.value for s in saved.scores if s.score_id == claim.score_id)
    for claim in result.findings[0].claims:
        assert claim.value == next(m.value for m in saved.metrics if m.metric_id == claim.metric_id)
    assert LIMIT in result.limitations
    assert set(result.findings[0].evidence_refs) <= {"evidence", "retrieved"}
    assert render_explanation(result, saved)
    return saved, result, text, backend


@pytest.mark.asyncio
async def test_why_this_index_names_structure_and_partial_coverage():
    saved, _, text, backend = await ask(linked_result(), "Почему такой индекс?")
    assert "Значение собрано из сохранённых компонентов расчёта" in text
    assert "полнота данных ограничена" in text
    assert not backend.calls and not re.search(r"\d", text)


@pytest.mark.asyncio
async def test_what_matters_most_stays_within_saved_structure():
    _, _, text, _ = await ask(linked_result(), "Что сильнее влияет на результат?")
    assert "компонентов расчёта" in text
    # No ranking of causes is invented from one index.
    assert "сильнее" not in text and "потому что" not in text and not re.search(r"\d", text)


@pytest.mark.asyncio
async def test_which_sources_lists_saved_provenance_and_found_documents():
    saved, result, text, backend = await ask(
        score_analysis(partial=True), "Какие источники использованы?", hits=[retrieved_hit()]
    )
    assert [call[0] for call in backend.calls] == ["search_evidence"]
    assert "источники сохранённого расчёта: source (получено 08.11.2026)" in text
    assert "Документальный контекст, не расчётный показатель: «Документ»" in text
    assert result.findings[0].evidence_refs == ["evidence", "retrieved"]
    assert "двести" not in text


@pytest.mark.asyncio
async def test_sources_without_documents_still_answers_from_the_saved_result():
    _, result, text, backend = await ask(
        score_analysis(partial=True), "Какие источники использованы?"
    )
    assert len(backend.calls) == 1 and result.retrieved_evidence == []
    assert "источники сохранённого расчёта" in text and "Документальный контекст" not in text


@pytest.mark.asyncio
async def test_unknown_route_is_not_turned_into_zero_or_no_route():
    saved = score_analysis(value=None, partial=True)
    _, result, text, _ = await ask(saved, "Почему маршрут неизвестен?")
    assert result.findings[0].score_claims[0].value is None
    assert "Неизвестное значение не означает ноль." in text
    assert "маршрута нет" not in text


@pytest.mark.asyncio
async def test_partial_coverage_is_explained_as_partial():
    _, _, text, _ = await ask(score_analysis(partial=True), "Что означает partial coverage?")
    assert "полнота данных ограничена" in text
    assert "не доказывают полноту всех объектов и путей города" in text


@pytest.mark.asyncio
async def test_compare_uses_only_two_compatible_saved_facts():
    _, result, text, _ = await ask(
        two_points(),
        "Сравни point и other-point",
        selection={"entity_kind": "sample_point", "entity_id": "point"},
        second={"score_id": "second"},
    )
    assert result.findings[0].score_ids == ["accessibility", "second"]
    assert "У второго показателя сохранённое значение выше." in text
    assert "а не рейтинг города" in text


@pytest.mark.asyncio
async def test_border_delay_without_evidence_is_declined():
    _, _, text, backend = await ask(movement(), "Почему задержка на границе?")
    assert text.startswith("В сохранённом расчёте нет оснований для такого вывода.")
    assert "границ" not in text and "задержк" not in text
    assert not backend.calls


@pytest.mark.asyncio
async def test_what_is_known_about_moscow_reports_the_saved_time_scope():
    _, result, text, _ = await ask(movement(), "Что известно о Москве по этому анализу?")
    assert "Это время движения; полное время цепочки оно не подтверждает." in text
    assert result.findings[0].claims[0].value == 1800


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Какие источники использованы?", True),
        ("На каком документе основана оценка?", True),
        ("Что говорит транспортная стратегия о времени в пути?", True),
        ("Какое распоряжение это регулирует?", True),
        ("Какая методика расчёта?", True),
        ("Почему такой индекс?", False),
        ("А H откуда берётся — из размера участка или всего города?", False),
        ("Сравни два результата", False),
        ("Почему задержка на границе?", False),
        ("", False),
    ],
)
def test_document_search_is_triggered_only_by_provenance_questions(question, expected):
    assert needs_documents(ExplanationRequest(question=question or None)) is expected
