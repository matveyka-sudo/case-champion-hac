"""Offline execution preserves evidence even when preflight or native replay fails."""

import argparse
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    examples = Path(__file__).parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "export_certified_walk_geometry.py"))


def test_exclusive_run_and_failed_partial_outputs_are_preserved(
    executor: dict[str, Any], tmp_path: Path
) -> None:
    def fail(_args: argparse.Namespace, output: Path) -> None:
        executor["write_new"](output / "export.jsonl", "partial native shape output")
        raise ValueError("native interrupted")

    run = executor["run"]
    run.__globals__["execute"] = fail
    args = argparse.Namespace(output_dir=tmp_path, run_id="immutable")
    with pytest.raises(ValueError, match="interrupted"):
        run(args)
    output = tmp_path / "runs/immutable"
    assert (output / "export.jsonl").read_text() == "partial native shape output"
    assert json.loads((output / "failure.json").read_text())["error_type"] == "ValueError"
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        run(args)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


@pytest.mark.parametrize("attack", ["hash", "parent", "absolute", "symlink"])
def test_local_source_references_reject_substitution(
    executor: dict[str, Any], tmp_path: Path, attack: str
) -> None:
    path = tmp_path / "source.json"
    path.write_text("immutable source")
    ref = {"path": "source.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert executor["reference"](tmp_path, ref) == path
    if attack == "hash":
        path.write_text("substituted source")
    elif attack == "parent":
        ref["path"] = "../source.json"
    elif attack == "absolute":
        ref["path"] = str(path)
    else:
        (tmp_path / "alias.json").symlink_to(path)
        ref["path"] = "alias.json"
    with pytest.raises(ValueError):
        executor["reference"](tmp_path, ref)
