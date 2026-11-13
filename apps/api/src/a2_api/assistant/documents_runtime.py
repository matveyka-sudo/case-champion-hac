"""Accepted documents as a separate corpus beside every snapshot the API serves.

Calculation snapshots stay byte-identical: their hashes are pinned by runtime
profiles and bindings. Each snapshot gets its own documents-only AcceptedData
(``<snapshot_id>:documents:<content hash>``) with the snapshot's territories, the evidence those
territories cite and the accepted documents (DOCUMENTS_DIR, see
a2_data_pipeline.documents). Chunks are published through evidence_cli with the
same acceptance checks; search covers the analysis snapshot and its corpus.
A federal document is context for every territory, never a calculated metric.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import EvidenceChunk
from a2_data_pipeline import documents

from a2_api.assistant import evidence_cli
from a2_api.core.registry import ProductRegistry
from a2_api.db.repositories.evidence import EvidenceRepository

logger = logging.getLogger("a2_api.documents")
SUFFIX = ":documents"


@dataclass(frozen=True)
class AcceptedDocument:
    meta: documents.DocumentMeta
    text: str
    start: str | None


@dataclass(frozen=True)
class DocumentCorpus:
    snapshot: AcceptedData
    chunks: tuple[EvidenceChunk, ...]
    documents_dir: Path

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(chunk.source_id for chunk in self.chunks)


def accepted_documents(documents_dir: Path) -> list[AcceptedDocument]:
    """Documents whose acceptance is complete; anything else is skipped and logged."""
    found = []
    for path in sorted(documents_dir.rglob("*.meta.json")):
        text_path = path.with_name(path.name.removesuffix(".meta.json") + ".txt")
        meta = documents.DocumentMeta.load(path)
        if not text_path.is_file():
            logger.warning("document_skipped evidence_id=%s reason=no_text", meta.evidence_id)
            continue
        text = text_path.read_bytes()
        try:
            documents.require_accepted(meta, text)
        except ValueError:
            logger.info("document_skipped evidence_id=%s reason=not_accepted", meta.evidence_id)
            continue
        start = json.loads(path.read_text(encoding="utf-8")).get("start")
        found.append(AcceptedDocument(meta, text.decode("utf-8"), start))
    return found


def _source_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_refs" and isinstance(item, list):
                yield from (str(ref) for ref in item)
            else:
                yield from _source_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _source_refs(item)


def corpus_snapshot(snapshot: AcceptedData, accepted: list[AcceptedDocument]) -> AcceptedData:
    """Deterministic documents-only snapshot: the same inputs give the same hash."""
    raw = snapshot.model_dump(mode="json")
    territories = [
        row for row in raw["territories"] if row["territory_id"] in raw["geographic_scope"]
    ]
    cited = set(_source_refs(territories))
    evidence = [row for row in raw["evidence"] if row["evidence_id"] in cited]
    versions = {row["source_id"]: raw["source_versions"][row["source_id"]] for row in evidence}
    for document in accepted:
        known = versions.get(document.meta.source_id)
        if known is not None and known != document.meta.source_version:
            raise ValueError(f"Snapshot pins another version of {document.meta.source_id}")
        versions[document.meta.source_id] = document.meta.source_version
        evidence.append(documents.document_evidence(document.meta).model_dump(mode="json"))
    accepted_at = max(document.meta.acceptance.accepted_at or date.min for document in accepted)
    empty: dict[str, list[object]] = {
        key: [] for key, value in raw.items() if isinstance(value, list)
    }
    draft = AcceptedData.model_validate(
        raw
        | empty
        | {
            "snapshot_id": snapshot.snapshot_id + SUFFIX,
            "source_versions": versions,
            "geographic_scope": [row["territory_id"] for row in territories],
            "territories": territories,
            "evidence": evidence,
            "qa": {
                "acceptance_state": "accepted",
                "accepted_at": f"{accepted_at.isoformat()}T00:00:00Z",
                "accepted_by": "documents-corpus",
                "checks": ["accepted_documents_only", "no_calculated_metrics"],
            },
        }
    )
    # Every content version is its own corpus: a changed document or acceptance
    # never collides with chunks already published for the previous version.
    version = canonical_hash(draft.model_dump(mode="json"))[:12]
    return AcceptedData.model_validate(
        draft.model_dump(mode="json") | {"snapshot_id": f"{draft.snapshot_id}:{version}"}
    )


def build_corpora(registry: ProductRegistry, documents_dir: Path) -> dict[str, DocumentCorpus]:
    """One corpus per registered snapshot, keyed by the calculation snapshot id."""
    accepted = accepted_documents(documents_dir)
    if not accepted:
        return {}
    corpora = {}
    for snapshot_id, snapshot in registry.snapshots.items():
        try:
            corpus = corpus_snapshot(snapshot, accepted)
        except ValueError as error:
            logger.warning("document_corpus_skipped snapshot_id=%s reason=%s", snapshot_id, error)
            continue
        chunks = tuple(
            chunk
            for document in accepted
            for chunk in documents.chunks(
                corpus,
                document.meta,
                documents.sections(documents.clean_lines(document.text)[0], document.start),
            )
        )
        corpora[snapshot_id] = DocumentCorpus(corpus, chunks, documents_dir)
    return corpora


async def publish_corpora(pool: asyncpg.Pool, corpora: dict[str, DocumentCorpus]) -> dict[str, int]:
    """Idempotent publication at API start; a failure leaves search without documents."""
    published = {}
    repository = EvidenceRepository(pool)
    for corpus in corpora.values():
        expected = len(corpus.chunks)
        hash_ = canonical_hash(corpus.snapshot.model_dump(mode="json"))
        counts = await repository.published_counts(corpus.snapshot_id, hash_)
        if sum(counts.values()) != expected:
            await evidence_cli.publish(
                pool, corpus.snapshot, corpus.chunks, documents_dir=corpus.documents_dir
            )
        published[corpus.snapshot_id] = expected
    return published
