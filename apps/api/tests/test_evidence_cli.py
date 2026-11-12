"""Opt-in PostgreSQL: ML2 document build -> publication CLI -> Russian FTS search."""

import json
from pathlib import Path

import pytest
from a2_api.assistant import evidence_cli
from a2_api.core.errors import DomainError
from a2_api.db.repositories.evidence import EvidenceRepository
from a2_contracts.common import canonical_hash
from a2_contracts.v2.retrieval import EvidenceSearchRequest
from a2_data_pipeline import documents

ROOT = Path(__file__).resolve().parents[3]
TEXT = """I. Общие положения
Документ описывает транспортную доступность и пересадочные узлы городов.
II. Цели
Сокращение полного времени поездки за счёт согласования пересадок.
"""


def built(tmp_path):
    raw = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())
    (tmp_path / "accepted.json").write_text(json.dumps(raw.get("payload", raw)))
    (tmp_path / "document.txt").write_text(TEXT)
    (tmp_path / "meta.json").write_text(
        json.dumps(
            {
                "evidence_id": "synthetic-document:2026",
                "source_id": "synthetic_document",
                "source_version": "2026-11-01",
                "url": "https://example.org/document.pdf",
                "retrieved_at": "2026-11-12T00:00:00+00:00",
                "title": "Синтетический документ",
                "document_date": "2026-11-01",
                "valid_from": "2026-11-01",
                "valid_to": "2026-11-30",
                "license": "public-domain-official-act",
                "file_sha256": "0" * 64,
            }
        )
    )
    documents.build(
        tmp_path / "document.txt",
        tmp_path / "meta.json",
        tmp_path / "accepted.json",
        "synthetic-snapshot-docs1",
        tmp_path / "out",
    )
    return evidence_cli.read_inputs(tmp_path / "out/snapshot.json", tmp_path / "out/chunks.jsonl")


@pytest.mark.asyncio
async def test_published_document_is_found_by_russian_search(database, tmp_path):
    pool, _, _ = database
    snapshot, chunks = built(tmp_path)
    report = await evidence_cli.publish(pool, snapshot, chunks)
    assert report == {"snapshot_id": snapshot.snapshot_id, "sources": {"synthetic_document": 2}}
    # Publishing the same reviewed chunks again is idempotent.
    assert await evidence_cli.publish(pool, snapshot, chunks) == report
    result = await EvidenceRepository(pool).search(
        EvidenceSearchRequest(
            query="На каком документе основано согласование пересадки?", territory_ids=["city-1"]
        ),
        snapshot_hashes={snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))},
        source_ids=frozenset({"synthetic_document"}),
    )
    # A natural question matches on any of its words, not only on all of them.
    assert result.status == "available"
    assert "II. Цели" in [hit.section for hit in result.hits]
    assert {hit.evidence_id for hit in result.hits} == {"synthetic-document:2026"}


@pytest.mark.asyncio
async def test_chunks_of_another_snapshot_are_refused(database, tmp_path):
    pool, _, _ = database
    snapshot, chunks = built(tmp_path)
    other = snapshot.model_copy(update={"snapshot_id": "another-snapshot"})
    with pytest.raises(DomainError):
        await evidence_cli.publish(pool, other, chunks)
    assert (await evidence_cli.status(pool, snapshot))["sources"] == {}


def test_reader_rejects_duplicate_chunks(tmp_path):
    snapshot, chunks = built(tmp_path)
    path = tmp_path / "dup.jsonl"
    path.write_text("".join(c.model_dump_json() + "\n" for c in [chunks[0], chunks[0]]))
    with pytest.raises(ValueError):
        evidence_cli.read_inputs(tmp_path / "out/snapshot.json", path)
