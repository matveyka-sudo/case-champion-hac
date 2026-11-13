"""Backend half of the citation roundtrip with apps/web (BUG-003/004/005).

Builds real assistant answers that cite documents and pins them as the web
fixture apps/web/tests/fixtures/api/explanation-documents.json; the web half
(tests/explanation-documents.test.ts) runs the browser's explanationSources on
the very same JSON. Regenerate with UPDATE_WEB_FIXTURES=1 after a deliberate
contract change.
"""

import json
import os
from pathlib import Path

import pytest
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import Backend, assistant, reply

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "apps/web/tests/fixtures/api/explanation-documents.json"


def saved() -> AnalysisResult:
    path = ROOT / "packages/contracts/fixtures/v2/urban_passenger-result.json"
    return AnalysisResult.model_validate(json.loads(path.read_text())["payload"])


def hit(chunk, section, **change):
    return {
        "chunk_id": f"strategy:{chunk}",
        "evidence_id": "strategy-document",
        "snapshot_id": "demo-snapshot",
        "source_id": "demo-source",
        "source_version": "version",
        "url": "https://example.org/strategy.pdf",
        "retrieved_at": "2026-11-08T09:00:00Z",
        "title": "Синтетическая стратегия",
        "section": section,
        "document_date": "2026-11-08",
        "valid_from": "2026-11-01",
        "valid_to": "2026-11-30",
        "territory_ids": ["demo-city"],
        "excerpt": f"Текст раздела {section}.",
        "content_hash": "hash",
    } | change


async def answer(result, hits, evidence_ids):
    backend = Backend(
        dict(
            status="available", completeness="partial", reason_code=None, hits=hits, truncated=False
        )
    )
    agent, *_ = assistant(
        [
            reply(
                {
                    "clause_id": "sources",
                    "findings": [{"metric_id": "demo-time"}],
                    "evidence_ids": evidence_ids,
                }
            )
        ],
        backend=backend,
        config=FlashConfig(
            api_key="TEST_ONLY",
            folder_id="TEST_ONLY",
            document_retrieval=True,
            max_model_calls=1,
            max_retries=0,
        ),
    )
    explanation = await agent.explain(
        result,
        access_scope=AccessScope(
            principal_id="owner",
            analysis_ids={result.analysis_id},
            territory_ids={"demo-city"},
            snapshot_ids={result.snapshot_id},
            source_ids={"demo-source"},
        ),
        registered_template_ids=set(),
        request_id="web-fixture",
        context=ExplanationRequest(question="На каком документе основаны цели по пересадкам?"),
    )
    assert explanation.status == "generated", explanation.reason_code
    data = explanation.model_dump(mode="json")
    data["model_id"] = "synthetic-model"
    return data


async def build() -> dict[str, object]:
    result = saved()
    # Sections arrive out of order; the public reference keeps chunk order.
    sections = [hit("0003", "Раздел C"), hit("0001", "Раздел A"), hit("0002", "Раздел B")]
    leaf = result.evidence_refs[0]
    same_leaf = hit(
        "0009",
        "Другой раздел сохранённого источника",
        evidence_id=leaf.evidence_id,
        url=str(leaf.url),
        retrieved_at=leaf.retrieved_at.isoformat().replace("+00:00", "Z"),
    )
    return {
        "result": result.model_dump(mode="json"),
        "new_document": await answer(result, sections, ["demo-evidence", "strategy-document"]),
        "saved_document": await answer(result, [same_leaf], ["demo-evidence"]),
    }


@pytest.mark.asyncio
async def test_web_fixture_is_what_the_backend_returns():
    built = await build()
    text = json.dumps(built, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_WEB_FIXTURES") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, (
        "Backend citations changed: regenerate the web fixture with UPDATE_WEB_FIXTURES=1 "
        "and run the web test"
    )
    new = built["new_document"]
    assert [ref["evidence_id"] for ref in new["retrieved_evidence"]] == ["strategy-document"]
    assert new["retrieved_evidence"][0]["section"] == "Раздел A; Раздел B; Раздел C"
    assert new["findings"][0]["evidence_refs"] == ["demo-evidence", "strategy-document"]
    same = built["saved_document"]
    assert same["retrieved_evidence"] == [built["result"]["evidence_refs"][0]]
