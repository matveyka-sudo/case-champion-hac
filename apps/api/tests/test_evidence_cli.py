"""Opt-in PostgreSQL: accepted document -> publication CLI -> Russian FTS search.

Publication needs the data owner's acceptance record next to the reviewed text;
a hand-set "verified" flag in a snapshot is not enough (BUG-010).
"""

import hashlib
import json
from pathlib import Path

import pytest
from a2_api.assistant import evidence_cli
from a2_api.core.errors import DomainError
from a2_api.db.repositories.evidence import EvidenceRepository
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import EvidenceChunk, EvidenceSearchRequest
from a2_data_pipeline import documents

ROOT = Path(__file__).resolve().parents[3]
TEXT = """I. Общие положения
Документ описывает транспортную доступность и пересадочные узлы городов.
II. Цели
Сокращение полного времени поездки за счёт согласования пересадок.
"""


def meta_record(**acceptance):
    return {
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
        "start": "I. Общие положения",
        "acceptance": {
            "status": "accepted",
            "accepted_by": "ML2",
            "accepted_at": "2026-11-12",
            "source_url_checked_at": "2026-11-12T00:00:00+00:00",
            "privacy_reviewed_at": "2026-11-12",
            "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest(),
        }
        | acceptance,
    }


def built(tmp_path):
    raw = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())
    (tmp_path / "accepted.json").write_text(json.dumps(raw.get("payload", raw)))
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "synthetic.txt").write_text(TEXT)
    (docs / "synthetic.meta.json").write_text(json.dumps(meta_record()))
    documents.build(
        docs / "synthetic.txt",
        docs / "synthetic.meta.json",
        tmp_path / "accepted.json",
        "synthetic-snapshot-docs1",
        tmp_path / "out",
        "I. Общие положения",
    )
    snapshot, chunks = evidence_cli.read_inputs(
        tmp_path / "out/snapshot.json", tmp_path / "out/chunks.jsonl"
    )
    return snapshot, chunks, docs


@pytest.mark.asyncio
async def test_published_document_is_found_by_russian_search(database, tmp_path):
    pool, _, _ = database
    snapshot, chunks, docs = built(tmp_path)
    report = await evidence_cli.publish(pool, snapshot, chunks, documents_dir=docs)
    assert report == {"snapshot_id": snapshot.snapshot_id, "sources": {"synthetic_document": 2}}
    # Publishing the same reviewed chunks again is idempotent.
    assert await evidence_cli.publish(pool, snapshot, chunks, documents_dir=docs) == report
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
    snapshot, chunks, docs = built(tmp_path)
    other = snapshot.model_copy(update={"snapshot_id": "another-snapshot"})
    with pytest.raises(DomainError):
        await evidence_cli.publish(pool, other, chunks, documents_dir=docs)
    assert (await evidence_cli.status(pool, snapshot))["sources"] == {}


def test_reader_rejects_duplicate_chunks(tmp_path):
    snapshot, chunks, _ = built(tmp_path)
    path = tmp_path / "dup.jsonl"
    path.write_text("".join(c.model_dump_json() + "\n" for c in [chunks[0], chunks[0]]))
    with pytest.raises(ValueError):
        evidence_cli.read_inputs(tmp_path / "out/snapshot.json", path)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("no_record", "No acceptance record"),
        ("pending", "not accepted"),
        ("no_privacy_review", "not read by a person"),
        ("text_changed", "differs from the reviewed text"),
        ("no_text", "Reviewed text"),
        ("url", "differs from its acceptance"),
        ("file_hash", "differs from its acceptance"),
        ("not_verified", "as verified"),
        ("foreign_text", "not part of the accepted text"),
        ("foreign_section", "not part of the accepted text"),
    ],
)
def test_publication_refuses_documents_without_owner_acceptance(tmp_path, change, reason):
    snapshot, chunks, docs = built(tmp_path)
    meta = docs / "synthetic.meta.json"
    if change == "no_record":
        meta.unlink()
    elif change == "pending":
        meta.write_text(json.dumps(meta_record(status="pending")))
    elif change == "no_privacy_review":
        meta.write_text(json.dumps(meta_record(privacy_reviewed_at=None)))
    elif change == "text_changed":
        (docs / "synthetic.txt").write_text(TEXT + "III. Новое\nДобавлено позже.\n")
    elif change == "no_text":
        (docs / "synthetic.txt").unlink()
    elif change in {"url", "file_hash", "not_verified"}:
        raw = snapshot.model_dump(mode="json")
        row = next(e for e in raw["evidence"] if e["evidence_id"] == "synthetic-document:2026")
        if change == "url":
            row["url"] = "https://example.org/other.pdf"
        elif change == "file_hash":
            row["content_hash"] = "1" * 64
        else:
            row["verified"] = False
        snapshot = AcceptedData.model_validate(raw)
    else:
        update = (
            {
                "text": "Синтетический текст не из документа.",
                "content_hash": canonical_hash("Синтетический текст не из документа."),
            }
            if change == "foreign_text"
            else {"section": "IX. Не из документа"}
        )
        chunks = [chunks[0].model_copy(update=update), *chunks[1:]]
    with pytest.raises(ValueError, match=reason):
        evidence_cli.require_accepted_documents(snapshot, chunks, docs)


def test_repository_corpus_is_not_publishable_until_accepted(tmp_path):
    """3363-r in data/documents: refused while pending, published once accepted."""
    path = ROOT / "data/documents/transport_strategy/3363-r-2021-11-27.meta.json"
    meta = documents.DocumentMeta.load(path)
    text = path.with_name("3363-r-2021-11-27.txt").read_text(encoding="utf-8")
    raw = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())
    snapshot = AcceptedData.model_validate(raw.get("payload", raw))
    section, piece = documents.reviewed_pieces(text, "I. Общие положения")[0]
    chunk = EvidenceChunk(
        schema_version="2",
        chunk_id=f"{meta.evidence_id}:0001",
        evidence_id=meta.evidence_id,
        snapshot_id=snapshot.snapshot_id,
        source_id=meta.source_id,
        source_version=meta.source_version,
        url=meta.url,
        retrieved_at=meta.retrieved_at,
        title=meta.title[:300],
        section=section,
        document_date=meta.document_date,
        valid_from=meta.valid_from,
        valid_to=meta.valid_to,
        territory_ids=snapshot.geographic_scope,
        text=piece,
        content_hash=canonical_hash(piece),
        public_nonpersonal_reviewed=True,
    )
    if meta.acceptance.status != "accepted":
        with pytest.raises(ValueError, match="not accepted"):
            evidence_cli.require_accepted_documents(snapshot, [chunk], ROOT / "data/documents")
    else:
        # Accepted: the gate passes once the snapshot cites the document as verified.
        with pytest.raises(ValueError, match="as verified"):
            evidence_cli.require_accepted_documents(snapshot, [chunk], ROOT / "data/documents")
