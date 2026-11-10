"""Download a source file into data/raw once, hash it and write its SourceManifest."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from .manifest import DatasetManifest, load_registry, write_dataset_manifest
from .paths import raw_dir, repo_root
from .store import freeze_raw

USER_AGENT = "a2-data-pipeline/0.1 (hackathon research; contact via repository)"


def download(url: str, target: Path, timeout: float = 600.0) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    headers = {"User-Agent": USER_AGENT}
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=timeout
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    partial.rename(target)


def fetch_dataset(
    *,
    dataset_id: str,
    source_id: str,
    url: str,
    filename: str,
    geography: str,
    coverage: str,
    limitations: list[str],
    schema_mapping: dict[str, str] | None = None,
    publication_date: date | None = None,
    upstream_checksum: str | None = None,
) -> DatasetManifest:
    """Fetch ``url`` unless the raw file already exists; raw files are never replaced."""

    entry = load_registry().get(source_id)
    target = raw_dir() / source_id / filename
    retrieved_at = datetime.now(UTC)
    if not target.exists():
        download(url, target)
    else:
        retrieved_at = datetime.fromtimestamp(target.stat().st_mtime, UTC)
    checksum = freeze_raw(target)
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        retrieved_at=retrieved_at,
        publication_date=publication_date,
        valid_from=None,
        valid_to=None,
        geography=geography,
        license=entry.license,
        access_method=entry.access_method,
        raw_path=str(target.relative_to(raw_dir())),
        checksum=checksum,
        upstream_checksum=upstream_checksum,
        schema_mapping=schema_mapping or {},
        coverage=coverage,
        limitations=limitations,
        status="fetched",
    )
    write_dataset_manifest(manifest)
    return manifest


def relative_to_repo(path: Path) -> str:
    return str(path.resolve().relative_to(repo_root()))
