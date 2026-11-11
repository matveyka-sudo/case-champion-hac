"""Repository paths. ``A2_DATA_DIR`` overrides the data root (tests, other checkouts)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "product.json").is_file():
            return parent
    raise RuntimeError("repository root with config/product.json not found")


def data_dir() -> Path:
    override = os.environ.get("A2_DATA_DIR")
    return Path(override).resolve() if override else repo_root() / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def processed_dir() -> Path:
    return data_dir() / "processed"


def bundles_dir() -> Path:
    return data_dir() / "bundles"
