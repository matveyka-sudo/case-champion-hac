"""Immutable ML2 publication boundary and parameterized PostgreSQL Russian FTS."""

import json
from collections.abc import Mapping, Sequence
from datetime import date

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import (
    EvidenceChunk,
    EvidenceHit,
    EvidenceSearchRequest,
    EvidenceSearchResult,
)

from a2_api.core.errors import DomainError


class EvidenceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def publish(self, snapshot: AcceptedData, chunks: Sequence[EvidenceChunk]) -> None:
        """Trusted server ingestion only. ML2 owns source acquisition, QA and PII review."""
        if not chunks or len(chunks) > 200:
            raise DomainError(
                422, "invalid_request", "Publish between one and 200 reviewed chunks per batch"
            )
        accepted = AcceptedData.model_validate(snapshot.model_dump(mode="json"))
        snapshot_hash = canonical_hash(accepted.model_dump(mode="json"))
        evidence = {record.evidence_id: record for record in accepted.evidence}
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for supplied in chunks:
                    chunk = EvidenceChunk.model_validate(supplied.model_dump(mode="json"))
                    source = evidence.get(chunk.evidence_id)
                    if (
                        source is None
                        or not source.verified
                        or chunk.snapshot_id != accepted.snapshot_id
                        or chunk.source_id != source.source_id
                        or chunk.source_version != source.source_version
                        or chunk.url != source.url
                        or chunk.retrieved_at != source.retrieved_at
                        or not set(chunk.territory_ids) <= set(accepted.geographic_scope)
                        or source.valid_period.start is None
                        or source.valid_period.end is None
                        or not source.valid_period.start
                        <= chunk.valid_from
                        <= chunk.valid_to
                        <= source.valid_period.end
                    ):
                        raise DomainError(
                            409,
                            "incompatible_versions",
                            "Document provenance is outside the accepted snapshot",
                        )
                    payload = chunk.model_dump(mode="json")
                    digest = canonical_hash(payload)
                    await connection.execute(
                        "INSERT INTO evidence_chunks(snapshot_id,chunk_id,snapshot_hash,source_id,territory_ids,"
                        "valid_from,valid_to,title,section,body,payload,record_hash) "
                        "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12) "
                        "ON CONFLICT(snapshot_id,chunk_id) DO NOTHING",
                        chunk.snapshot_id,
                        chunk.chunk_id,
                        snapshot_hash,
                        chunk.source_id,
                        chunk.territory_ids,
                        chunk.valid_from,
                        chunk.valid_to,
                        chunk.title,
                        chunk.section,
                        chunk.text,
                        chunk.model_dump_json(),
                        digest,
                    )
                    existing = await connection.fetchrow(
                        "SELECT snapshot_hash,record_hash FROM evidence_chunks WHERE snapshot_id=$1 AND chunk_id=$2",
                        chunk.snapshot_id,
                        chunk.chunk_id,
                    )
                    if (
                        existing is None
                        or existing["snapshot_hash"] != snapshot_hash
                        or existing["record_hash"] != digest
                    ):
                        raise DomainError(
                            409,
                            "incompatible_versions",
                            "Accepted chunk identity already published",
                        )

    async def published_counts(self, snapshot_id: str, snapshot_hash: str) -> dict[str, int]:
        """Published chunks per source for one exact accepted snapshot version."""
        rows = await self.pool.fetch(
            "SELECT source_id,count(*) AS chunks FROM evidence_chunks "
            "WHERE snapshot_id=$1 AND snapshot_hash=$2 GROUP BY source_id ORDER BY source_id",
            snapshot_id,
            snapshot_hash,
        )
        return {str(row["source_id"]): int(row["chunks"]) for row in rows}

    async def search(
        self,
        request: EvidenceSearchRequest,
        *,
        snapshot_hashes: Mapping[str, str],
        source_ids: frozenset[str],
    ) -> EvidenceSearchResult:
        request = EvidenceSearchRequest.model_validate(request.model_dump(mode="json"))
        empty = EvidenceSearchResult(
            status="unknown", reason_code="EVIDENCE_INDEX_UNAVAILABLE", hits=[]
        )
        if not snapshot_hashes or not source_ids:
            return empty
        # A JSON map binds each authorized snapshot to its exact accepted hash.
        hashes = json.dumps(dict(snapshot_hashes))
        exists = await self.pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM evidence_chunks WHERE snapshot_hash=$1::jsonb->>snapshot_id "
            "AND source_id=ANY($2::text[]) AND territory_ids && $3::text[])",
            hashes,
            sorted(source_ids),
            request.territory_ids,
        )
        if not exists:
            return empty
        year_start = date(request.year, 1, 1) if request.year else None
        year_end = date(request.year, 12, 31) if request.year else None
        # A natural question rarely contains every word of a fragment, so any word
        # may match. Fragments are ordered by the rarity of the matched words
        # inside the authorized scope (an IDF weight), so words common to the
        # whole corpus ("транспорт") do not outrank the specific ones.
        rows = await self.pool.fetch(
            "WITH query AS (SELECT replace(plainto_tsquery('russian'::regconfig,$1)::text,"
            "' & ',' | ')::tsquery AS q,"
            "tsvector_to_array(to_tsvector('russian'::regconfig,$1)) AS terms),"
            "scoped AS (SELECT snapshot_id,chunk_id,payload,search_vector FROM evidence_chunks "
            "WHERE snapshot_hash=$2::jsonb->>snapshot_id AND source_id=ANY($3::text[]) "
            "AND territory_ids && $4::text[] AND ($5::text[] IS NULL OR source_id=ANY($5)) "
            "AND ($6::date IS NULL OR valid_to>=$6) AND ($7::date IS NULL OR valid_from<=$7) "
            "AND ($8::date IS NULL OR valid_to>=$8) AND ($9::date IS NULL OR valid_from<=$9)),"
            "total AS (SELECT count(*)::float8 AS n FROM scoped),"
            "df AS (SELECT u.term,count(*)::float8 AS n FROM scoped "
            "CROSS JOIN LATERAL unnest(tsvector_to_array(scoped.search_vector)) AS u(term),query "
            "WHERE u.term=ANY(query.terms) GROUP BY u.term),"
            "scores AS (SELECT s.snapshot_id,s.chunk_id,sum(ln((total.n+1)/(df.n+0.5))) AS score "
            "FROM scoped s CROSS JOIN total "
            "CROSS JOIN LATERAL unnest(tsvector_to_array(s.search_vector)) AS u(term) "
            "JOIN df ON df.term=u.term GROUP BY s.snapshot_id,s.chunk_id) "
            "SELECT s.payload,sc.score,ts_rank_cd(s.search_vector,query.q) AS rank "
            "FROM scores sc JOIN scoped s USING (snapshot_id,chunk_id) CROSS JOIN query "
            "WHERE s.search_vector @@ query.q "
            "ORDER BY sc.score DESC,rank DESC,s.snapshot_id,s.chunk_id LIMIT 6",
            request.query,
            hashes,
            sorted(source_ids),
            request.territory_ids,
            request.source_ids,
            request.start_date,
            request.end_date,
            year_start,
            year_end,
        )
        hits = []
        for row in rows[:5]:
            chunk = EvidenceChunk.model_validate_json(row["payload"])
            payload = chunk.model_dump(
                mode="json", exclude={"schema_version", "text", "public_nonpersonal_reviewed"}
            )
            payload["territory_ids"] = sorted(set(chunk.territory_ids) & set(request.territory_ids))
            payload["excerpt"] = chunk.text[:1200]
            hits.append(EvidenceHit.model_validate(payload))
        result = EvidenceSearchResult(
            status="available", reason_code=None, hits=hits, truncated=len(rows) > 5
        )
        while (
            result.hits
            and len(
                json.dumps(
                    {"untrusted_tool_data": result.model_dump(mode="json")}, ensure_ascii=False
                ).encode()
            )
            > 11000
        ):
            result.hits.pop()
            result.truncated = True
        return result
