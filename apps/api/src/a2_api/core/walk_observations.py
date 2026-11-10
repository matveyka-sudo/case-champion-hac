"""Operator-owned shared observation storage, independent of city or requests."""

import os
from pathlib import Path


def observation_directory(*, legacy: bool = False) -> Path:
    configured = (
        os.environ.get("A2_WALK_PILOT_EVIDENCE_DIR")
        if legacy
        else os.environ.get("A2_WALK_OBSERVATION_DIR")
        or os.environ.get("A2_WALK_PILOT_EVIDENCE_DIR")
    )
    if not configured:
        raise ValueError("WALK_OBSERVATION_DIRECTORY_REQUIRED")
    folder = Path(configured).resolve()
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        raise ValueError("WALK_OBSERVATION_DIRECTORY_UNAVAILABLE")
    return folder
