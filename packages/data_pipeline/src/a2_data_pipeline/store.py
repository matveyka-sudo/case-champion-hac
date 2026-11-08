"""Immutable raw files, atomic bundle publication and ``load_bundle``."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .bundle import BundleData, BundleManifest, FileRef, read_collection
from .manifest import Checksum
from .paths import bundles_dir

BUNDLE_COLLECTIONS = (
    "evidence",
    "territories",
    "population_cells",
    "sample_points",
    "destinations",
    "category_ownership",
)


class BundleError(Exception):
    code = "bundle_error"


class BundleNotFoundError(BundleError, KeyError):
    code = "unknown_id"


class BundleNotAcceptedError(BundleError):
    code = "bundle_not_accepted"


class BundleIntegrityError(BundleError):
    code = "bundle_integrity"


def sha256_file(path: Path) -> Checksum:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return Checksum(algorithm="sha256", value=digest.hexdigest(), size_bytes=path.stat().st_size)


def freeze_raw(path: Path) -> Checksum:
    """Record a raw file's hash and make it read-only; raw inputs are never edited."""

    checksum = sha256_file(path)
    path.chmod(0o444)
    return checksum


def _dump(rows: Sequence[BaseModel | Mapping[str, Any]]) -> bytes:
    data = [
        row.model_dump(mode="json") if isinstance(row, BaseModel) else dict(row) for row in rows
    ]
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def publish_bundle(
    manifest: BundleManifest,
    collections: Mapping[str, Sequence[BaseModel | Mapping[str, Any]]],
    qa_report: Mapping[str, Any],
    root: Path | None = None,
) -> Path:
    """Write a bundle into a temporary directory, validate it, then rename atomically.

    Published bundles are immutable: an existing ``bundle_id`` is never overwritten.
    """

    base = root or bundles_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = base / manifest.bundle_id
    if target.exists():
        raise BundleError(f"bundle {manifest.bundle_id} already published; use a new id")
    missing = set(BUNDLE_COLLECTIONS) - set(collections)
    if missing:
        raise BundleError(f"missing collections: {sorted(missing)}")
    staging = base / f".staging-{manifest.bundle_id}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        files: dict[str, FileRef] = {}
        for name in BUNDLE_COLLECTIONS:
            payload = _dump(collections[name])
            path = staging / f"{name}.json"
            path.write_bytes(payload)
            files[name] = FileRef(
                path=path.name,
                sha256=hashlib.sha256(payload).hexdigest(),
                rows=len(collections[name]),
            )
        qa_bytes = (json.dumps(qa_report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (staging / manifest.qa_report).write_bytes(qa_bytes)
        final = manifest.model_copy(update={"files": files})
        manifest_text = json.dumps(final.model_dump(mode="json"), ensure_ascii=False, indent=2)
        (staging / "manifest.json").write_text(manifest_text + "\n", encoding="utf-8")
        _load_dir(staging, require_accepted=False)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _load_dir(directory: Path, require_accepted: bool) -> BundleData:
    manifest = BundleManifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    if require_accepted and manifest.acceptance_state != "accepted":
        raise BundleNotAcceptedError(
            f"bundle {manifest.bundle_id} is {manifest.acceptance_state}, not accepted"
        )
    payload: dict[str, Any] = {"manifest": manifest}
    for name in BUNDLE_COLLECTIONS:
        ref = manifest.files.get(name)
        if ref is None:
            raise BundleIntegrityError(f"manifest lists no file for {name}")
        path = directory / ref.path
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != ref.sha256:
            raise BundleIntegrityError(f"{name}: sha256 mismatch")
        rows = read_collection(path)
        if len(rows) != ref.rows:
            raise BundleIntegrityError(f"{name}: row count mismatch")
        payload[name] = rows
    return BundleData.model_validate(payload)


def list_bundles(root: Path | None = None) -> list[str]:
    base = root or bundles_dir()
    if not base.is_dir():
        return []
    return sorted(
        child.name
        for child in base.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / "manifest.json").is_file()
    )


def load_bundle(
    bundle_id: str, *, allow_unaccepted: bool = False, root: Path | None = None
) -> BundleData:
    """Load and verify a published bundle.

    Raises ``BundleNotFoundError`` (code ``unknown_id``) for an unknown id and
    ``BundleNotAcceptedError`` unless the bundle is accepted. Hashes, row counts
    and references are re-checked on every load.
    """

    base = root or bundles_dir()
    directory = base / bundle_id
    if bundle_id.startswith(".") or "/" in bundle_id or not (directory / "manifest.json").is_file():
        raise BundleNotFoundError(bundle_id)
    return _load_dir(directory, require_accepted=not allow_unaccepted)
