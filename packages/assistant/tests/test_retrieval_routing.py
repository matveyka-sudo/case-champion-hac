"""Reproducible document-search routing: one bounded search, only for a named topic."""

import json

import pytest
from a2_assistant.context.retrieval import (
    content_words,
    document_intent,
    needs_documents,
    search_query,
)
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from test_assistant import Backend, assistant, reply, retrieved_hit, score_analysis

ROUTED = {
    "overview": "Расскажи про транспортную ситуацию в районе",
    "sources": "Откуда сведения о пересадках в этом районе?",
    "official": "Что говорит транспортная стратегия о Калининградской области?",
    "methodology": "Какой норматив времени для поликлиник?",
    "causes": "Почему задержка на границе?",
    "context": "Что ограничивает доступность поликлиник?",
}
SAVED_ONLY = [
    "Почему такой индекс?",
    "Какие источники использованы?",
    "Какая методика расчёта?",
    "Сравни point и other-point",
    "У какой точки индекс выше?",
    "Насколько больше время во втором результате?",
]


@pytest.mark.parametrize("intent,question", ROUTED.items())
def test_each_intent_with_a_topic_gets_a_search(intent, question):
    context = ExplanationRequest(question=question)
    assert document_intent(context) == intent
    assert needs_documents(context)


@pytest.mark.parametrize("question", SAVED_ONLY)
def test_questions_about_the_saved_result_only_are_answered_without_search(question):
    assert not needs_documents(ExplanationRequest(question=question))


def test_query_keeps_the_topic_and_drops_question_intent_and_result_words():
    assert search_query("Что говорит транспортная стратегия о Калининградской области?") == (
        "транспортная стратегия калининградской области"
    )
    assert search_query("Почему такой индекс у поликлиник?") == "поликлиник"
    assert content_words("Какие источники использованы?") == []
    # A question with no topic keeps its text, but needs_documents never sends it.
    assert search_query("Почему такой индекс?") == "Почему такой индекс?"


async def run(question, hits):
    saved = score_analysis(partial=True)
    backend = Backend(
        dict(
            status="available", completeness="partial", reason_code=None, hits=hits, truncated=False
        )
    )
    agent, transport, _, budget = assistant(
        [reply({"clause_id": "coverage", "findings": [{"score_id": "accessibility"}]})],
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
        request_id="routing",
        context=ExplanationRequest(question=question),
    )
    return result, transport, backend, budget


@pytest.mark.asyncio
async def test_the_cleaned_query_is_sent_once_and_documents_stay_bounded():
    many = [
        retrieved_hit() | {"chunk_id": f"doc:{i:04d}", "excerpt": "Пересадка. " * 100}
        for i in range(5)
    ]
    result, transport, backend, budget = await run("Что известно о пересадках в районе?", many)
    assert [call[0] for call in backend.calls] == ["search_evidence"]
    assert backend.calls[0][1]["query"] == "пересадках районе"
    assert len(transport.calls) == len(budget.reservations) == 1
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert all(len(doc["excerpt"]) <= 400 for doc in facts["documents"])
    assert len(json.dumps(facts["documents"], ensure_ascii=False).encode()) <= 2400
    assert facts["document_context"]["truncated"] is True
    assert "not_calculated_metrics" in facts["document_context"]["kind"]
    assert result.status == "generated"


@pytest.mark.asyncio
@pytest.mark.parametrize("hits", [[], None], ids=["empty", "index_unavailable"])
async def test_empty_or_unavailable_index_adds_no_document_claim(hits):
    if hits is None:
        result, transport, backend, _ = await run_unavailable()
    else:
        result, transport, backend, _ = await run("Что известно о пересадках в районе?", hits)
    assert len(backend.calls) == 1 and len(transport.calls) == 1
    assert result.status == "generated" and result.retrieved_evidence == []
    assert "Документальный контекст" not in result.findings[0].text
    assert result.findings[0].evidence_refs == ["evidence"]


async def run_unavailable():
    saved = score_analysis(partial=True)
    agent, transport, backend, budget = assistant(
        [reply({"clause_id": "coverage", "findings": [{"score_id": "accessibility"}]})],
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
        request_id="routing-unavailable",
        context=ExplanationRequest(question="Что известно о пересадках в районе?"),
    )
    return result, transport, backend, budget
