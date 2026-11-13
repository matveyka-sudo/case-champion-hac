"""Accepted documents as a corpus beside each calculation snapshot (DOCUMENTS_DIR)."""

import json
import shutil
from pathlib import Path

import pytest
from a2_api.assistant.documents_runtime import SUFFIX, build_corpora, publish_corpora
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData

from apps.api.tests.v2_handlers import registry

ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS = ROOT / "data/documents"
database = pytest.importorskip("apps.api.tests.conftest").database


def snapshot_hash(snapshot: AcceptedData) -> str:
    return canonical_hash(snapshot.model_dump(mode="json"))


def test_calculation_snapshots_stay_identical_and_get_a_corpus():
    catalog = registry()
    before = {sid: snapshot_hash(s) for sid, s in catalog.snapshots.items()}
    corpora = build_corpora(catalog, DOCUMENTS)
    assert {sid: snapshot_hash(s) for sid, s in catalog.snapshots.items()} == before
    corpus = corpora["synthetic-snapshot"]
    assert corpus.snapshot_id.startswith("synthetic-snapshot" + SUFFIX + ":")
    assert corpus.snapshot.geographic_scope == ["city-1"]
    assert corpus.source_ids == {"transport_strategy"}
    assert len(corpus.chunks) > 100
    assert all(chunk.territory_ids == ["city-1"] for chunk in corpus.chunks)
    # No calculation rows: the corpus holds territories and documents only.
    assert not corpus.snapshot.destinations and not corpus.snapshot.scheduled_services
    again = build_corpora(registry(), DOCUMENTS)["synthetic-snapshot"]
    assert snapshot_hash(again.snapshot) == snapshot_hash(corpus.snapshot)


def test_documents_without_acceptance_are_left_out(tmp_path):
    copy = tmp_path / "documents"
    shutil.copytree(DOCUMENTS, copy)
    meta = next(copy.rglob("*.meta.json"))
    raw = json.loads(meta.read_text())
    raw["acceptance"]["status"] = "pending"
    meta.write_text(json.dumps(raw, ensure_ascii=False))
    assert build_corpora(registry(), copy) == {}


def test_a_territory_citing_another_version_of_the_source_gets_no_corpus():
    catalog = registry()
    raw = catalog.snapshots["synthetic-snapshot"].model_dump(mode="json")
    # The territory itself cites evidence of the document source at another version.
    raw["source_versions"]["transport_strategy"] = "another-version"
    raw["evidence"].append(
        raw["evidence"][0]
        | {
            "evidence_id": "strategy-old",
            "source_id": "transport_strategy",
            "source_version": "another-version",
        }
    )
    raw["territories"][0]["source_refs"].append("strategy-old")
    catalog.snapshots["synthetic-snapshot"] = AcceptedData.model_validate(raw)
    assert build_corpora(catalog, DOCUMENTS) == {}


def test_calculation_pins_of_unused_sources_do_not_reach_the_corpus():
    catalog = registry()
    raw = catalog.snapshots["synthetic-snapshot"].model_dump(mode="json")
    raw["source_versions"]["unused-source"] = "v9"
    catalog.snapshots["synthetic-snapshot"] = AcceptedData.model_validate(raw)
    corpus = build_corpora(catalog, DOCUMENTS)["synthetic-snapshot"]
    assert "unused-source" not in corpus.snapshot.source_versions


@pytest.mark.asyncio
async def test_started_api_answers_with_the_published_document(database):
    import httpx
    from a2_api.api.application import create_app
    from a2_api.assistant.tools import SavedTools
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService
    from a2_api.workers.runner import Worker
    from a2_assistant import FlashAssistant, FlashConfig
    from test_assistant import FakeTransport, MemoryBudget, reply

    pool, _, _ = database
    catalog = registry()
    corpora = build_corpora(catalog, DOCUMENTS)
    published = await publish_corpora(pool, corpora)
    corpus_id = corpora["synthetic-snapshot"].snapshot_id
    assert published == {corpus_id: len(corpora["synthetic-snapshot"].chunks)}
    # A second start publishes nothing new.
    assert await publish_corpora(pool, corpora) == published
    rows = await pool.fetchval(
        "SELECT count(*) FROM evidence_chunks WHERE snapshot_id=$1", corpus_id
    )
    assert rows == len(corpora["synthetic-snapshot"].chunks)

    repository = JobRepository(pool)
    service = AnalysisService(catalog, repository, code_version="demo-code", engine_enabled=True)
    service.document_corpora = corpora
    body = json.loads(
        (ROOT / "packages/contracts/fixtures/v2/urban_passenger-request.json").read_text()
    )["payload"] | {"city_id": "city-1"}
    app = create_app(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        submitted = await client.post(
            "/api/v1/analyses", json=body, headers={"Idempotency-Key": "documents-corpus"}
        )
        assert submitted.status_code == 202, submitted.text
        worker = Worker(
            repository,
            handler="test_postgres_retrieval:matching_evidence_result",
            code_version="demo-code",
            timeout_seconds=5,
        )
        assert await worker.run_once()
        job = (await client.get("/api/v1/jobs/" + submitted.json()["job_id"])).json()
        assert job["status"] == "succeeded", job
        saved = (await client.get("/api/v1/analyses/" + job["analysis_id"])).json()
        metric = saved["metrics"][0]
        transport = FakeTransport(
            [reply({"clause_id": "sources", "findings": [{"metric_id": metric["metric_id"]}]})]
        )
        service.assistant = FlashAssistant(
            FlashConfig(
                api_key="TEST_ONLY",
                folder_id="TEST_ONLY",
                document_retrieval=True,
                max_model_calls=1,
                max_retries=0,
            ),
            transport=transport,
            tool_backend=SavedTools(service),
            deployment_budget=MemoryBudget(),
        )
        response = await client.post(
            "/api/v1/analyses/" + job["analysis_id"] + "/explanation",
            json={"question": "Что говорит транспортная стратегия о пересадках?"},
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "generated", result
    documents = [
        ref for ref in result["retrieved_evidence"] if ref["source_id"] == "transport_strategy"
    ]
    assert [ref["evidence_id"] for ref in documents] == ["transport_strategy:3363-r:2021-11-27"]
    finding = result["findings"][0]
    assert "transport_strategy:3363-r:2021-11-27" in finding["evidence_refs"]
    assert "Документальный контекст, не расчётный показатель" in finding["text"]
    assert finding["claims"][0]["value"] == metric["value"]
    sent = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert sent["documents"] and all(doc["snapshot_id"] == corpus_id for doc in sent["documents"])


@pytest.mark.asyncio
async def test_a_changed_document_version_publishes_beside_the_old_one(database, tmp_path):
    pool, _, _ = database
    first = build_corpora(registry(), DOCUMENTS)["synthetic-snapshot"]
    await publish_corpora(pool, {"synthetic-snapshot": first})
    copy = tmp_path / "documents"
    shutil.copytree(DOCUMENTS, copy)
    meta = next(copy.rglob("*.meta.json"))
    raw = json.loads(meta.read_text())
    raw["acceptance"]["accepted_at"] = "2026-11-14"
    meta.write_text(json.dumps(raw, ensure_ascii=False))
    second = build_corpora(registry(), copy)["synthetic-snapshot"]
    assert second.snapshot_id != first.snapshot_id
    # No identity conflict with the chunks already published for the old version.
    assert await publish_corpora(pool, {"synthetic-snapshot": second}) == {
        second.snapshot_id: len(second.chunks)
    }


@pytest.mark.asyncio
async def test_retrieval_accepts_only_this_snapshot_or_its_own_corpus():
    from a2_assistant.context.retrieval import retrieve
    from a2_assistant.tools import AccessScope
    from a2_contracts.v2.assistant import ExplanationRequest
    from test_assistant import Backend, retrieved_hit, score_analysis

    saved = score_analysis(partial=True)
    question = ExplanationRequest(question="Что говорит стратегия о пересадках?")
    for snapshot_id, ok in (
        (saved.snapshot_id + SUFFIX + ":abc123def456", True),
        (saved.snapshot_id, True),
        ("other-snapshot" + SUFFIX + ":abc123def456", False),
        (saved.snapshot_id + "-documents", False),
    ):
        hit = retrieved_hit() | {"snapshot_id": snapshot_id}
        backend = Backend(
            dict(
                status="available",
                completeness="partial",
                reason_code=None,
                hits=[hit],
                truncated=False,
            )
        )
        scope = AccessScope(
            principal_id="owner",
            analysis_ids={saved.analysis_id},
            territory_ids={"city"},
            snapshot_ids={saved.snapshot_id, snapshot_id},
            source_ids={"source"},
        )
        if ok:
            assert (await retrieve(backend, saved, scope, question)).hits
        else:
            with pytest.raises(ValueError):
                await retrieve(backend, saved, scope, question)


@pytest.mark.asyncio
async def test_without_a_model_the_template_answer_still_lists_found_documents(database):
    """LLM_ENABLED=false on a stand: no model answer, the documents are still shown."""
    import httpx
    from a2_api.api.application import create_app
    from a2_api.db.repositories.jobs import JobRepository
    from a2_api.services.analyses import AnalysisService
    from a2_api.workers.runner import Worker

    pool, _, _ = database
    catalog = registry()
    corpora = build_corpora(catalog, DOCUMENTS)
    await publish_corpora(pool, corpora)
    repository = JobRepository(pool)
    service = AnalysisService(catalog, repository, code_version="demo-code", engine_enabled=True)
    service.document_corpora = corpora
    assert service.assistant is None
    body = json.loads(
        (ROOT / "packages/contracts/fixtures/v2/urban_passenger-request.json").read_text()
    )["payload"] | {"city_id": "city-1"}
    app = create_app(service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        submitted = await client.post(
            "/api/v1/analyses", json=body, headers={"Idempotency-Key": "documents-template"}
        )
        worker = Worker(
            repository,
            handler="test_postgres_retrieval:matching_evidence_result",
            code_version="demo-code",
            timeout_seconds=5,
        )
        assert await worker.run_once()
        job = (await client.get("/api/v1/jobs/" + submitted.json()["job_id"])).json()
        endpoint = "/api/v1/analyses/" + job["analysis_id"] + "/explanation"
        with_topic = (
            await client.post(endpoint, json={"question": "Что говорит стратегия о пересадках?"})
        ).json()
        no_topic = (await client.post(endpoint, json={"question": "Почему такой индекс?"})).json()
    assert with_topic["status"] == "template" and with_topic["reason_code"] == "DISABLED"
    assert [ref["evidence_id"] for ref in with_topic["retrieved_evidence"]] == [
        "transport_strategy:3363-r:2021-11-27"
    ]
    # The template's own findings and numbers are untouched by the document.
    assert all(
        "transport_strategy" not in ref
        for f in with_topic["findings"]
        for ref in f["evidence_refs"]
    )
    assert no_topic["status"] == "template" and no_topic["retrieved_evidence"] == []
