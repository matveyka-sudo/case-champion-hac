"""Server-side document lookup without model tools (one model call, no tool schemas)."""

import json

import pytest
from a2_assistant.core import render_explanation
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from test_assistant import Backend, assistant, reply, retrieved_hit, score_analysis

SCORE = {"score_id": "accessibility", "explanation_code": "saved_value"}


async def run(*, retrieval, question, response=None, hits=None):
    saved = score_analysis(partial=True)
    backend = Backend(
        dict(
            status="available",
            completeness="partial",
            reason_code=None,
            hits=[retrieved_hit()] if hits is None else hits,
            truncated=False,
        )
    )
    agent, transport, _, budget = assistant(
        [reply(response or {"clause_id": "coverage", "findings": [SCORE]})],
        backend=backend,
        config=FlashConfig(
            api_key="TEST_ONLY",
            folder_id="TEST_ONLY",
            tools_enabled=False,
            document_retrieval=retrieval,
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
        request_id="documents",
        context=ExplanationRequest(question=question),
    )
    return saved, result, transport, backend, budget


@pytest.mark.asyncio
async def test_documents_are_found_without_model_tools():
    saved, result, transport, backend, budget = await run(
        retrieval=True, question="На каком документе основана оценка?"
    )
    assert [call[0] for call in backend.calls] == ["search_evidence"]
    assert len(transport.calls) == len(budget.reservations) == 1
    assert transport.calls[0]["tools"] == []
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert [doc["evidence_id"] for doc in facts["documents"]] == ["retrieved"]
    assert "not_calculated_metrics" in facts["document_context"]["kind"]
    assert result.status == "generated"
    assert [ref.evidence_id for ref in result.retrieved_evidence] == ["retrieved"]
    # A document never becomes a saved metric value.
    assert result.findings[0].score_claims[0].value == saved.scores[0].value
    assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_copied_document_id_is_accepted_and_foreign_one_is_not():
    question = "Какой документ лежит в основе?"
    *_, ok, _, _, _ = await run(
        retrieval=True,
        question=question,
        response={"clause_id": "coverage", "findings": [SCORE], "evidence_ids": ["retrieved"]},
    )
    assert ok.status == "generated"
    *_, bad, _, _, _ = await run(
        retrieval=True,
        question=question,
        response={"clause_id": "coverage", "findings": [SCORE], "evidence_ids": ["other-doc"]},
    )
    assert bad.status == "template" and bad.reason_code == "INVALID_EXPLANATION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retrieval,question",
    [(False, "На каком документе основана оценка?"), (True, "Почему индекс такой?")],
)
async def test_no_lookup_when_switched_off_or_not_asked(retrieval, question):
    _, result, transport, backend, _ = await run(retrieval=retrieval, question=question)
    assert backend.calls == [] and len(transport.calls) == 1
    assert result.retrieved_evidence == []


@pytest.mark.asyncio
async def test_empty_index_still_answers_from_saved_facts():
    _, result, transport, backend, _ = await run(
        retrieval=True, question="Какой источник у оценки?", hits=[]
    )
    assert len(backend.calls) == 1 and len(transport.calls) == 1
    assert result.status == "generated" and result.retrieved_evidence == []
