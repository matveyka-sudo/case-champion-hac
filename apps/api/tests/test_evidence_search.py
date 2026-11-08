"""Russian evidence retrieval on accepted, synthetic-only document chunks."""

from datetime import date

import pytest
from a2_api.evidence_search import EvidenceRepository
from a2_api.services import DomainError
from a2_contracts.common import canonical_hash
from a2_contracts.v2.retrieval import EvidenceChunk, EvidenceSearchRequest
from pydantic import ValidationError
from v2_handlers import registry


def accepted():
    return registry().snapshots["synthetic-snapshot"]


def chunk(**changes):
    source = accepted().evidence[0]
    text = (
        "Синтетический документ: пересадка между автобусами. Подтверждены ограничения расписания."
    )
    payload = dict(
        schema_version="2",
        chunk_id="synthetic-chunk",
        evidence_id=source.evidence_id,
        snapshot_id="synthetic-snapshot",
        source_id=source.source_id,
        source_version=source.source_version,
        url=str(source.url),
        retrieved_at=source.retrieved_at,
        title="Синтетические пересадки",
        section="Расписание",
        document_date=date(2026, 11, 8),
        valid_from=date(2026, 11, 1),
        valid_to=date(2026, 11, 30),
        territory_ids=["city-1"],
        text=text,
        content_hash=canonical_hash(text),
        public_nonpersonal_reviewed=True,
    )
    payload.update(changes)
    return EvidenceChunk.model_validate(payload)


@pytest.mark.asyncio
async def test_russian_full_text_search_returns_real_accepted_provenance_and_bounded_excerpt(
    database,
):
    pool, _, _ = database
    repo = EvidenceRepository(pool)
    snapshot = accepted()
    await repo.publish(snapshot, [chunk()])
    result = await repo.search(
        EvidenceSearchRequest(query="пересадки автобуса", territory_ids=["city-1"]),
        snapshot_hashes={snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))},
        source_ids=frozenset({"source-1"}),
    )
    assert result.status == "available"
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.evidence_id == "e-1" and hit.source_id == "source-1"
    assert str(hit.url) == str(snapshot.evidence[0].url)
    assert hit.section == "Расписание"
    assert hit.excerpt in chunk().text
    assert len(hit.excerpt) <= 1200
    assert hit.content_hash == chunk().content_hash
    assert result.completeness == "partial"


@pytest.mark.asyncio
async def test_retrieval_filters_sources_periods_territories_and_immutable_snapshot_hash(database):
    pool, _, _ = database
    repo = EvidenceRepository(pool)
    snapshot = accepted()
    await repo.publish(snapshot, [chunk()])
    kwargs = dict(
        snapshot_hashes={snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))},
        source_ids=frozenset({"source-1"}),
    )
    base = dict(query="пересадка", territory_ids=["city-1"])
    for changes in (
        {"source_ids": ["other"]},
        {"year": 2025},
        {"start_date": date(2027, 1, 1), "end_date": date(2027, 1, 31)},
        {"territory_ids": ["other"]},
    ):
        assert (await repo.search(EvidenceSearchRequest(**(base | changes)), **kwargs)).hits == []
    stale = await repo.search(
        EvidenceSearchRequest(**base),
        snapshot_hashes={snapshot.snapshot_id: "different-hash"},
        source_ids=frozenset({"source-1"}),
    )
    assert stale.status == "unknown" and stale.reason_code == "EVIDENCE_INDEX_UNAVAILABLE"
    assert (await repo.search(EvidenceSearchRequest(**(base | {"year": 2026})), **kwargs)).hits
    assert not (
        await repo.search(
            EvidenceSearchRequest(query="' OR 1=1 --", territory_ids=["city-1"]), **kwargs
        )
    ).hits


@pytest.mark.asyncio
async def test_publication_is_immutable_validates_provenance_and_never_ingests_unapproved_content(
    database,
):
    pool, _, _ = database
    repo = EvidenceRepository(pool)
    snapshot = accepted()
    await repo.publish(snapshot, [chunk()])
    await repo.publish(snapshot, [chunk()])
    for changes in (
        {"source_id": "invented"},
        {"evidence_id": "invented"},
        {"url": "https://example.org/wrong"},
        {"territory_ids": ["other"]},
        {"valid_to": date(2027, 1, 1)},
    ):
        with pytest.raises(DomainError):
            await repo.publish(snapshot, [chunk(**changes)])
    with pytest.raises(DomainError) as conflict:
        await repo.publish(snapshot, [chunk(section="Changed immutable section")])
    assert conflict.value.status_code == 409
    with pytest.raises(ValidationError):
        chunk(text="forged hash")
    with pytest.raises(ValidationError):
        chunk(public_nonpersonal_reviewed=False)
    assert await pool.fetchval("SELECT count(*) FROM evidence_chunks") == 1


@pytest.mark.asyncio
async def test_absent_index_is_unknown_and_empty_search_does_not_mean_absent_transport(database):
    pool, _, _ = database
    repo = EvidenceRepository(pool)
    result = await repo.search(
        EvidenceSearchRequest(query="пересадка", territory_ids=["city-1"]),
        snapshot_hashes={"synthetic-snapshot": "hash"},
        source_ids=frozenset({"source-1"}),
    )
    assert result.status == "unknown" and result.hits == []
    assert result.reason_code == "EVIDENCE_INDEX_UNAVAILABLE"


@pytest.mark.parametrize(
    "changes",
    [
        {"query": " "},
        {"territory_ids": []},
        {"source_ids": []},
        {"start_date": date(2026, 1, 1)},
        {"start_date": date(2026, 2, 1), "end_date": date(2026, 1, 1)},
    ],
)
def test_search_rejects_ambiguous_or_unbounded_filters(changes):
    with pytest.raises(ValidationError):
        EvidenceSearchRequest.model_validate(
            dict(query="пересадка", territory_ids=["city-1"]) | changes
        )


@pytest.mark.asyncio
async def test_cyrillic_retrieval_respects_actual_tool_byte_budget(database):
    import json

    pool, _, _ = database
    repo = EvidenceRepository(pool)
    snapshot = accepted()
    text = "пересадка " * 600
    await repo.publish(
        snapshot,
        [
            chunk(chunk_id=f"synthetic-{i}", text=text, content_hash=canonical_hash(text))
            for i in range(7)
        ],
    )
    result = await repo.search(
        EvidenceSearchRequest(query="пересадка", territory_ids=["city-1"]),
        snapshot_hashes={snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))},
        source_ids=frozenset({"source-1"}),
    )
    assert (
        len(
            json.dumps(
                {"untrusted_tool_data": result.model_dump(mode="json")}, ensure_ascii=False
            ).encode()
        )
        <= 12000
    )
    assert result.truncated is True
    assert 1 <= len(result.hits) < 5


@pytest.mark.asyncio
async def test_evidence_migration_refuses_destructive_downgrade_when_documents_exist(database):
    import asyncio

    from apps.api.tests.migration_support import migrate

    pool, schema, url = database
    await EvidenceRepository(pool).publish(accepted(), [chunk()])
    with pytest.raises(RuntimeError, match="accepted evidence"):
        await asyncio.to_thread(migrate, url, schema, "0002_v2_jobs", True)
    assert await pool.fetchval("SELECT count(*) FROM evidence_chunks") == 1


@pytest.mark.asyncio
async def test_document_period_is_not_conflated_with_transport_snapshot_period(database):
    from a2_contracts.v2.dataset import AcceptedData

    pool, _, _ = database
    repo = EvidenceRepository(pool)
    raw = accepted().model_dump(mode="json")
    raw["evidence"][0]["valid_period"] = {"start": "2025-01-01", "end": "2025-12-31"}
    snapshot = AcceptedData.model_validate(raw)
    reviewed = chunk(
        valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31), document_date=date(2025, 6, 1)
    )
    await repo.publish(snapshot, [reviewed])
    result = await repo.search(
        EvidenceSearchRequest(query="пересадка", territory_ids=["city-1"], year=2025),
        snapshot_hashes={snapshot.snapshot_id: canonical_hash(snapshot.model_dump(mode="json"))},
        source_ids=frozenset({"source-1"}),
    )
    assert result.hits and result.hits[0].document_date.year == 2025
