"""Opt-in PostgreSQL: retrieval quality on fixed questions over the 3363-r candidate text.

TEST ONLY: the text is published into a throwaway test schema under a synthetic
snapshot, never into a working database. Production publication goes through
evidence_cli and the documents corpus (a2_api.assistant.documents_runtime). The thresholds
record the measured quality (13.11.2026) so ranking regressions are visible.

Measured on 945 chunks, 14 questions (relevant = the chunk contains the topic):
  question as is, ts_rank_cd only            top1 5/14  precision@5 0.26
  rarity weight (this repository)            top1 6/14  precision@5 0.37
  rarity weight + search_query (assistant)   top1 9/14  precision@5 0.40
"""

import json
from pathlib import Path

import pytest
from a2_api.db.repositories.evidence import EvidenceRepository
from a2_assistant.context.retrieval import search_query
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import EvidenceChunk, EvidenceSearchRequest
from a2_data_pipeline import documents

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "data/documents/transport_strategy/3363-r-2021-11-27"
QUESTIONS = [
    ("Что говорит транспортная стратегия о Калининградской области?", ["калининград"]),
    ("Какие цели по сокращению времени в пути?", ["время в пути", "времени в пути"]),
    ("Какие принципы развития пассажирских перевозок?", ["пассажирск"]),
    ("Что планируется для грузовых перевозок и транзита?", ["транзит"]),
    ("Как будет развиваться цифровизация транспорта?", ["цифров"]),
    ("Какие проблемы транспортной инфраструктуры?", ["проблем"]),
    ("Как снизить воздействие транспорта на окружающую среду?", ["окружающ", "экологи"]),
    ("Откуда финансируется развитие транспорта?", ["финансир", "инвестиц"]),
    ("Какие этапы реализации стратегии?", ["этап"]),
    ("Что сказано о безопасности на транспорте?", ["безопасност"]),
    ("Что с морскими портами в Арктике?", ["арктик"]),
    ("Что известно о транспортной доступности агломераций?", ["агломерац"]),
    ("Почему в городе важна доступность общественного транспорта?", ["общественн"]),
    ("Какие ограничения у данных о пассажирских перевозках?", ["пассажирск"]),
]


async def publish_candidate(pool):
    raw = json.loads((ROOT / "packages/contracts/fixtures/v2/accepted-data.json").read_text())
    base = raw.get("payload", raw)
    meta = documents.DocumentMeta.load(DOC.with_suffix(".meta.json"))
    base["snapshot_id"] = "retrieval-quality-test-only"
    base["source_versions"] |= {meta.source_id: meta.source_version}
    base["evidence"].append(
        {
            "evidence_id": meta.evidence_id,
            "source_id": meta.source_id,
            "source_version": meta.source_version,
            "url": meta.url,
            "retrieved_at": meta.retrieved_at.isoformat(),
            "valid_period": {"start": "2021-11-27", "end": "2035-12-31"},
            "license": meta.license,
            "content_hash": meta.file_sha256,
            "verified": True,  # TEST ONLY, disposable schema
        }
    )
    snapshot = AcceptedData.model_validate(base)
    text = DOC.with_suffix(".txt").read_text(encoding="utf-8")
    chunks = [
        EvidenceChunk(
            schema_version="2",
            chunk_id=f"{meta.evidence_id}:{index:04d}",
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
        for index, (section, piece) in enumerate(
            documents.reviewed_pieces(text, "I. Общие положения"), 1
        )
    ]
    repository = EvidenceRepository(pool)
    for start in range(0, len(chunks), 200):
        await repository.publish(snapshot, chunks[start : start + 200])
    return repository, snapshot, meta


async def measure(repository, snapshot, meta, build):
    top1 = precision = 0.0
    for question, terms in QUESTIONS:
        result = await repository.search(
            EvidenceSearchRequest(
                query=build(question)[:500],
                territory_ids=snapshot.geographic_scope,
                start_date=snapshot.valid_period.start,
                end_date=snapshot.valid_period.start,
            ),
            snapshot_hashes={
                snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))
            },
            source_ids=frozenset({meta.source_id}),
        )
        relevant = [any(term in hit.excerpt.lower() for term in terms) for hit in result.hits]
        assert result.hits, question
        top1 += relevant[0]
        precision += sum(relevant) / 5
    return top1, precision / len(QUESTIONS)


@pytest.mark.asyncio
async def test_rarity_ranking_and_cleaned_query_keep_the_measured_quality(database):
    pool, _, _ = database
    repository, snapshot, meta = await publish_candidate(pool)
    raw_top1, raw_precision = await measure(repository, snapshot, meta, lambda q: q)
    top1, precision = await measure(repository, snapshot, meta, search_query)
    assert raw_top1 >= 6 and raw_precision >= 0.35
    assert top1 >= 9 and precision >= 0.38
    assert (top1, precision) >= (raw_top1, raw_precision)
