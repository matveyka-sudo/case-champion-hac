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
    assert corpus.snapshot_id == "synthetic-snapshot" + SUFFIX
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
    assert published == {"synthetic-snapshot" + SUFFIX: len(corpora["synthetic-snapshot"].chunks)}
    # A second start publishes nothing new.
    assert await publish_corpora(pool, corpora) == published
    rows = await pool.fetchval(
        "SELECT count(*) FROM evidence_chunks WHERE snapshot_id=$1", "synthetic-snapshot" + SUFFIX
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
    assert sent["documents"] and all(
        doc["snapshot_id"] == "synthetic-snapshot" + SUFFIX for doc in sent["documents"]
    )
