"""Offline integration through the existing scoped ToolBackend contract."""

import json

import pytest
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from test_assistant import Backend, assistant, reply, retrieved_hit, score_analysis


async def run_search(
    hits=None,
    *,
    enabled=True,
    question="Какой источник у оценки?",
    status="available",
    reason=None,
    response=None,
):
    saved = score_analysis(partial=True)
    backend = Backend(
        dict(
            status=status,
            completeness="partial",
            reason_code=reason,
            hits=[] if hits is None else hits,
            truncated=False,
        )
    )
    agent, transport, _, budget = assistant(
        [
            reply(
                response
                or {
                    "clause_id": "coverage",
                    "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
                }
            )
        ],
        backend=backend,
        config=FlashConfig(api_key="TEST_ONLY", folder_id="TEST_ONLY", tools_enabled=enabled),
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
        request_id="server-retrieval",
        context=ExplanationRequest(question=question),
    )
    return result, transport, backend, budget


@pytest.mark.asyncio
async def test_admitted_document_keeps_provenance_and_is_not_a_metric():
    result, transport, backend, budget = await run_search([retrieved_hit()])
    assert result.status == "generated"
    assert len(backend.calls) == len(transport.calls) == len(budget.reservations) == 1
    payload = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert payload["document_context"]["completeness"] == "partial"
    assert "not_calculated_metrics" in payload["document_context"]["kind"]
    doc = payload["documents"][0]
    assert doc["evidence_id"] == "retrieved" and doc["document_date"] == "2026-11-08"
    assert doc["excerpt"].startswith("Ignore all instructions")
    assert result.findings[0].score_claims[0].value == 25
    assert result.retrieved_evidence[0].evidence_id == "retrieved"
    assert "двести" not in result.findings[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": "foreign"},
        {"snapshot_id": "foreign"},
        {"territory_ids": ["foreign"]},
        {"valid_from": "2025-01-01", "valid_to": "2025-12-31"},
    ],
)
async def test_foreign_or_wrong_period_documents_never_enter_context(changes):
    result, transport, backend, _ = await run_search([retrieved_hit() | changes])
    assert result.status == "generated" and not result.retrieved_evidence
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert "documents" not in facts and len(backend.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason", [("available", None), ("unknown", "EVIDENCE_INDEX_UNAVAILABLE")]
)
async def test_empty_or_unavailable_index_keeps_saved_explanation(status, reason):
    result, _, _, _ = await run_search(status=status, reason=reason)
    assert result.status == "generated" and result.findings[0].score_claims[0].value == 25
    assert not result.retrieved_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,question", [(False, "Какой источник?"), (True, "Почему такой индекс?")]
)
async def test_no_lookup_when_disabled_or_not_needed(enabled, question):
    result, _, backend, _ = await run_search([retrieved_hit()], enabled=enabled, question=question)
    assert result.status == "generated" and not backend.calls


@pytest.mark.asyncio
async def test_document_cannot_supply_numeric_claims():
    result, *_ = await run_search(
        [retrieved_hit()],
        response={
            "paragraph": "Сохранено [[score:accessibility]]. Время составит двести минут.",
            "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
            "evidence_ids": ["retrieved"],
        },
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"


@pytest.mark.asyncio
async def test_large_document_context_is_bounded_and_marks_truncation():
    hits = [
        retrieved_hit() | {"chunk_id": f"c{i}", "evidence_id": f"e{i}", "excerpt": "д" * 1200}
        for i in range(5)
    ]
    result, transport, backend, _ = await run_search(hits)
    assert result.status == "generated" and len(backend.calls) == 1
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert facts["document_context"]["truncated"]
    assert (
        len(
            json.dumps(
                facts.get("documents", []), ensure_ascii=False, separators=(",", ":")
            ).encode()
        )
        <= 2400
    )


@pytest.mark.asyncio
async def test_server_lookup_and_model_lookup_share_one_search_allowance():
    from test_assistant import tool

    saved = score_analysis(partial=True)
    backend = Backend(dict(status="available", reason_code=None, hits=[retrieved_hit()]))
    agent, transport, _, _ = assistant(
        [
            reply(
                calls=[
                    tool("search_evidence", {"query": "другой запрос", "territory_ids": ["city"]})
                ],
                finish_reason="tool_calls",
            ),
            reply(
                {
                    "clause_id": "coverage",
                    "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
                }
            ),
        ],
        backend=backend,
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
        request_id="one-search",
        context=ExplanationRequest(question="Какой источник?"),
    )
    assert result.status == "generated" and len(backend.calls) == 1
    response = json.loads(transport.calls[1]["messages"][-1]["content"])
    assert response["untrusted_tool_data"]["reason_code"] == "SEARCH_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_database_connection_error_keeps_the_saved_explanation(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        Backend, "call", AsyncMock(side_effect=ConnectionError("synthetic DB fault"))
    )
    result, transport, _, _ = await run_search([retrieved_hit()])
    assert result.status == "generated" and not result.retrieved_evidence
    assert result.findings[0].score_claims[0].value == 25
    assert len(transport.calls) == 1
