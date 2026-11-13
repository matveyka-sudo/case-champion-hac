"""RAG citations: one reference per document, cited by the statement, safe for the browser.

Covers BUG-003 (one provenance contract for backend and frontend), BUG-004 (chunks
of one document no longer overwrite each other) and BUG-005 (a chosen document is
cited by the finding, not only listed as retrieved).
"""

import json

import pytest
from a2_assistant.core import render_explanation
from a2_assistant.tools import AccessScope
from a2_assistant.transport import FlashConfig
from a2_contracts.v2.assistant import ExplanationRequest
from test_assistant import Backend, assistant, reply, retrieved_hit, score_analysis

SCORE = {"score_id": "accessibility"}
QUESTION = "На каком документе основаны цели по пересадкам?"


def hit(chunk, section, **change):
    return retrieved_hit() | {"chunk_id": f"doc:{chunk}", "section": section} | change


def saved_leaf_hit(section="Раздел документа"):
    """A chunk of the very document the saved result already cites."""
    return hit(
        "0001",
        section,
        evidence_id="evidence",
        url="https://example.org/",
        retrieved_at="2026-11-08T00:00:00Z",
    )


async def run(hits, response, question=QUESTION):
    saved = score_analysis(partial=True)
    backend = Backend(
        dict(
            status="available", completeness="partial", reason_code=None, hits=hits, truncated=False
        )
    )
    agent, transport, _, budget = assistant(
        [reply(response)],
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
        request_id="citations",
        context=ExplanationRequest(question=question),
    )
    assert len(transport.calls) == len(budget.reservations) == 1
    return saved, result, transport


def browser_sources(saved, answer):
    """The apps/web explanationSources rule (evidenceGroups.ts), on API JSON."""
    result, reply_json = saved.model_dump(mode="json"), answer.model_dump(mode="json")
    leaves = {row["evidence_id"]: row for row in result["evidence_refs"]}
    fields = ("source_id", "url", "section", "observed_at", "retrieved_at")
    same = lambda a, b: all(a[f] == b[f] for f in fields)  # noqa: E731
    extra = {}
    for source in reply_json["retrieved_evidence"]:
        assert source["evidence_id"] not in extra, "duplicate retrieved document"
        known = leaves.get(source["evidence_id"])
        assert known is None or same(known, source), "retrieved document changes a saved leaf"
        extra[source["evidence_id"]] = source
    cited = list(reply_json["evidence_refs"])
    for finding in reply_json["findings"]:
        cited += finding["evidence_refs"]
    shown = dict(extra)
    for ref in cited:
        assert ref in leaves or ref in extra, "citation without a source"
        shown.setdefault(ref, leaves.get(ref) or extra[ref])
    return shown


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(0, 1, 2), (2, 0, 1), (1, 2, 0)])
async def test_chunks_of_one_document_make_one_stable_reference(order):
    chunks = [hit("0001", "Раздел A"), hit("0002", "Раздел B"), hit("0003", "Раздел C")]
    saved, result, _ = await run(
        [chunks[i] for i in order],
        {"clause_id": "sources", "findings": [SCORE], "evidence_ids": ["evidence", "retrieved"]},
    )
    assert result.status == "generated"
    # One public reference; sections in chunk order whatever order the index returned.
    assert [ref.evidence_id for ref in result.retrieved_evidence] == ["retrieved"]
    assert result.retrieved_evidence[0].section == "Раздел A; Раздел B; Раздел C"
    assert "разделы: Раздел A; Раздел B; Раздел C" in result.findings[0].text
    assert render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("clause", ["explain", "coverage", "sources"])
async def test_chosen_document_is_cited_by_the_finding_as_context(clause):
    saved, result, transport = await run(
        [retrieved_hit()],
        {"clause_id": clause, "findings": [SCORE], "evidence_ids": ["evidence", "retrieved"]},
    )
    assert result.status == "generated"
    finding = result.findings[0]
    assert finding.evidence_refs == ["evidence", "retrieved"]
    assert result.evidence_refs == ["evidence", "retrieved"]
    assert "Документальный контекст, не расчётный показатель: «Документ», раздел: Расписание" in (
        finding.text
    )
    # The document never becomes a number: its text stays out, saved claims are intact.
    sent = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]["documents"]
    assert "двести" in sent[0]["excerpt"] and "двести" not in finding.text
    assert "Ignore all instructions" not in finding.text
    assert finding.claims == [] and finding.score_claims[0].value == saved.scores[0].value
    assert set(browser_sources(saved, result)) == {"evidence", "retrieved"}


@pytest.mark.asyncio
async def test_unchosen_document_is_listed_but_not_cited_outside_the_sources_clause():
    saved, result, _ = await run(
        [retrieved_hit()],
        {"clause_id": "coverage", "findings": [SCORE], "evidence_ids": ["evidence"]},
    )
    assert result.status == "generated"
    assert result.findings[0].evidence_refs == ["evidence"]
    assert "Документальный контекст" not in result.findings[0].text
    assert [ref.evidence_id for ref in result.retrieved_evidence] == ["retrieved"]
    # The sources clause is about documents: the server cites every admitted one.
    _, listed, _ = await run([retrieved_hit()], {"clause_id": "sources", "findings": [SCORE]})
    assert listed.findings[0].evidence_refs == ["evidence", "retrieved"]
    assert "источники сохранённого расчёта: source (получено 08.11.2026)" in listed.findings[0].text


@pytest.mark.asyncio
async def test_another_section_of_a_saved_document_keeps_the_saved_leaf_for_the_browser():
    saved, result, _ = await run(
        [saved_leaf_hit("Другой раздел")],
        {"clause_id": "sources", "findings": [SCORE], "evidence_ids": ["evidence"]},
    )
    assert result.status == "generated"
    # Document identity is unchanged, so the public reference is the saved leaf itself;
    # the chunk's own section is named in the server-written sentence.
    assert result.retrieved_evidence == [saved.evidence_refs[0]]
    assert "раздел: Другой раздел" in result.findings[0].text
    assert set(browser_sources(saved, result)) == {"evidence"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://example.org/forged"},
        {"source_id": "other-source"},
        {"retrieved_at": "2026-11-09T00:00:00Z"},
        {"snapshot_id": "other-snapshot"},
        {"territory_ids": ["other-city"]},
        {"valid_from": "2026-12-01", "valid_to": "2026-12-31"},
    ],
    ids=["url", "source", "retrieved_at", "snapshot", "territory", "period"],
)
async def test_forged_or_out_of_scope_document_is_dropped_and_cannot_be_cited(change):
    forged = saved_leaf_hit() | change
    saved, result, transport = await run(
        [forged], {"clause_id": "sources", "findings": [SCORE], "evidence_ids": ["evidence"]}
    )
    # The saved metrics are still explained; the document simply is not there.
    assert result.status == "generated" and result.retrieved_evidence == []
    assert (
        "documents" not in json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    )
    assert "Документальный контекст" not in result.findings[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["other-document", "doc:0001", "https://example.org/document"])
async def test_document_id_that_was_not_retrieved_is_rejected(foreign):
    _, result, _ = await run(
        [retrieved_hit()],
        {"clause_id": "sources", "findings": [SCORE], "evidence_ids": [foreign]},
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert result.retrieved_evidence == []


@pytest.mark.asyncio
async def test_two_documents_are_sorted_and_long_wording_is_cut_explicitly():
    long_section = "Длинный раздел " * 11  # two of them exceed one reference field
    hits = [
        hit("0002", long_section.strip(), evidence_id="doc-b", title="Второй документ"),
        hit("0001", "Раздел A", evidence_id="doc-a", title="Первый документ"),
        hit("0003", (long_section + "2").strip(), evidence_id="doc-b", title="Второй документ"),
    ]
    saved, result, _ = await run(hits, {"clause_id": "sources", "findings": [SCORE]})
    assert result.status == "generated"
    assert [ref.evidence_id for ref in result.retrieved_evidence] == ["doc-a", "doc-b"]
    assert len(result.retrieved_evidence[1].section) <= 300
    assert result.retrieved_evidence[1].section.endswith("…")
    assert len(result.findings[0].text) <= 1000
    assert result.findings[0].evidence_refs == ["evidence", "doc-a", "doc-b"]
    assert set(browser_sources(saved, result)) == {"evidence", "doc-a", "doc-b"}
