"""Operator publication of reviewed ML2 document chunks into the PostgreSQL FTS index.

Input is an accepted snapshot (AcceptedData JSON) and its reviewed chunks (JSON
lines of EvidenceChunk). Every chunk must cite verified document evidence of that
exact snapshot; EvidenceRepository.publish enforces it. Nothing is fetched here.

A "verified" flag in a snapshot is not enough: it can be set by hand. Each cited
document also needs the data owner's acceptance record in the documents folder
(``<name>.meta.json`` next to the reviewed ``<name>.txt``, see
a2_data_pipeline.documents). Its provenance must equal the snapshot evidence, and
every chunk must be a piece of exactly that reviewed text.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from a2_contracts.common import canonical_hash
from a2_contracts.v2.dataset import AcceptedData
from a2_contracts.v2.retrieval import EvidenceChunk
from a2_data_pipeline.documents import DocumentMeta, require_accepted, reviewed_pieces
from pydantic import HttpUrl, TypeAdapter, ValidationError

from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.db.connection import open_pool
from a2_api.db.repositories.evidence import EvidenceRepository

_BATCH = 200


def read_inputs(snapshot_path: Path, chunks_path: Path) -> tuple[AcceptedData, list[EvidenceChunk]]:
    snapshot = AcceptedData.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    chunks = [
        EvidenceChunk.model_validate_json(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not chunks:
        raise ValueError("No reviewed chunks to publish")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("Duplicate chunk identity")
    return snapshot, chunks


def _acceptance_records(documents_dir: Path) -> dict[str, Path]:
    records: dict[str, Path] = {}
    for path in sorted(documents_dir.rglob("*.meta.json")):
        evidence_id = DocumentMeta.load(path).evidence_id
        if evidence_id in records:
            raise ValueError(f"Two acceptance records for document {evidence_id}")
        records[evidence_id] = path
    return records


def require_accepted_documents(
    snapshot: AcceptedData, chunks: Sequence[EvidenceChunk], documents_dir: Path
) -> None:
    """Fail closed before any database write; nothing is published on error."""
    records = _acceptance_records(documents_dir)
    evidence = {row.evidence_id: row for row in snapshot.evidence}
    url = TypeAdapter(HttpUrl)
    for evidence_id in sorted({chunk.evidence_id for chunk in chunks}):
        path = records.get(evidence_id)
        if path is None:
            raise ValueError(f"No acceptance record for document {evidence_id}")
        text_path = path.with_name(path.name.removesuffix(".meta.json") + ".txt")
        if not text_path.is_file():
            raise ValueError(f"Reviewed text of document {evidence_id} is missing")
        meta = DocumentMeta.load(path)
        text = text_path.read_bytes()
        require_accepted(meta, text)
        row = evidence.get(evidence_id)
        if row is None or not row.verified:
            raise ValueError(f"Snapshot does not cite document {evidence_id} as verified")
        if (
            row.source_id,
            row.source_version,
            row.url,
            row.retrieved_at,
            row.content_hash,
        ) != (
            meta.source_id,
            meta.source_version,
            url.validate_python(meta.url),
            meta.retrieved_at,
            meta.file_sha256,
        ):
            raise ValueError(f"Snapshot provenance of {evidence_id} differs from its acceptance")
        start = json.loads(path.read_text(encoding="utf-8")).get("start")
        reviewed = set(reviewed_pieces(text.decode("utf-8"), start))
        for chunk in chunks:
            if chunk.evidence_id != evidence_id:
                continue
            if (chunk.section, chunk.text) not in reviewed or (
                chunk.source_id,
                chunk.source_version,
                chunk.url,
                chunk.retrieved_at,
            ) != (row.source_id, row.source_version, row.url, row.retrieved_at):
                raise ValueError(f"Chunk {chunk.chunk_id} is not part of the accepted text")


async def publish(
    pool: asyncpg.Pool,
    snapshot: AcceptedData,
    chunks: Sequence[EvidenceChunk],
    *,
    documents_dir: Path,
) -> dict[str, object]:
    require_accepted_documents(snapshot, chunks, documents_dir)
    repository = EvidenceRepository(pool)
    for start in range(0, len(chunks), _BATCH):
        await repository.publish(snapshot, chunks[start : start + _BATCH])
    return await status(pool, snapshot)


async def status(pool: asyncpg.Pool, snapshot: AcceptedData) -> dict[str, object]:
    counts = await EvidenceRepository(pool).published_counts(
        snapshot.snapshot_id, canonical_hash(snapshot.model_dump(mode="json"))
    )
    return {"snapshot_id": snapshot.snapshot_id, "sources": counts}


async def _execute(
    command: str, snapshot_path: Path, chunks_path: Path | None, documents_dir: Path
) -> object:
    if command == "publish":
        if chunks_path is None:
            raise ValueError("publish requires --chunks")
        snapshot, chunks = read_inputs(snapshot_path, chunks_path)
        # Checked before the database is opened.
        require_accepted_documents(snapshot, chunks, documents_dir)
    else:
        snapshot = AcceptedData.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    pool = await open_pool(RuntimeSettings())
    try:
        if command == "publish":
            return await publish(pool, snapshot, chunks, documents_dir=documents_dir)
        return await status(pool, snapshot)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "status"])
    parser.add_argument("--snapshot", type=Path, required=True, help="AcceptedData JSON")
    parser.add_argument("--chunks", type=Path, help="EvidenceChunk JSON lines")
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path("data/documents"),
        help="folder with acceptance records (*.meta.json) and reviewed texts (*.txt)",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_execute(args.command, args.snapshot, args.chunks, args.documents))
    except Exception as error:
        # Provenance errors are safe to show; anything else only by type.
        safe = isinstance(error, DomainError) or (
            isinstance(error, ValueError) and not isinstance(error, ValidationError)
        )
        reason = str(error) if safe else type(error).__name__
        parser.exit(1, f"Evidence command failed: {reason}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
