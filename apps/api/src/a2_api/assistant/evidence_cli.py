"""Operator publication of reviewed ML2 document chunks into the PostgreSQL FTS index.

Input is an accepted snapshot (AcceptedData JSON) and its reviewed chunks (JSON
lines of EvidenceChunk). Every chunk must cite verified document evidence of that
exact snapshot; EvidenceRepository.publish enforces it. Nothing is fetched here.
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
from pydantic import ValidationError

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


async def publish(
    pool: asyncpg.Pool, snapshot: AcceptedData, chunks: Sequence[EvidenceChunk]
) -> dict[str, object]:
    repository = EvidenceRepository(pool)
    for start in range(0, len(chunks), _BATCH):
        await repository.publish(snapshot, chunks[start : start + _BATCH])
    return await status(pool, snapshot)


async def status(pool: asyncpg.Pool, snapshot: AcceptedData) -> dict[str, object]:
    rows = await pool.fetch(
        "SELECT source_id,count(*) AS chunks FROM evidence_chunks "
        "WHERE snapshot_id=$1 AND snapshot_hash=$2 GROUP BY source_id ORDER BY source_id",
        snapshot.snapshot_id,
        canonical_hash(snapshot.model_dump(mode="json")),
    )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "sources": {row["source_id"]: row["chunks"] for row in rows},
    }


async def _execute(command: str, snapshot_path: Path, chunks_path: Path | None) -> object:
    if command == "publish":
        if chunks_path is None:
            raise ValueError("publish requires --chunks")
        snapshot, chunks = read_inputs(snapshot_path, chunks_path)
    else:
        snapshot = AcceptedData.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    pool = await open_pool(RuntimeSettings())
    try:
        if command == "publish":
            return await publish(pool, snapshot, chunks)
        return await status(pool, snapshot)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "status"])
    parser.add_argument("--snapshot", type=Path, required=True, help="AcceptedData JSON")
    parser.add_argument("--chunks", type=Path, help="EvidenceChunk JSON lines")
    args = parser.parse_args()
    try:
        result = asyncio.run(_execute(args.command, args.snapshot, args.chunks))
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
