"""Execution refuses clobbering evidence and redirected graph inputs before Docker."""

import argparse
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def executor() -> dict[str, Any]:
    return runpy.run_path(str(Path(__file__).parents[1] / "examples/audit_source_bound_walk.py"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def source_fixture(root: Path, image: str) -> dict[str, Any]:
    (root / "tiles/2/000/000").mkdir(parents=True)
    tile = "actual-source-tile"
    (root / "tiles/2/000/000/001.gph").write_text(tile)
    config = json.dumps({"mjolnir": {"tile_dir": "/data/tiles", "tile_extract": "/data/tiles.tar"}})
    profile = json.dumps({"pedestrian_options": {"walking_speed": 5, "type": "foot"}})
    (root / "valhalla.json").write_text(config)
    (root / "walking-profile.json").write_text(profile)
    (root / "valhalla-build-manifest.json").write_text(
        json.dumps(
            {
                "image_id": image,
                "config_sha256": sha(config),
                "profile_sha256": sha(profile),
                "files": [{"path": "tiles/2/000/000/001.gph", "sha256": sha(tile)}],
            }
        )
    )
    return {"build": {"pedestrian_options_json": json.dumps({"walking_speed": 5, "type": "foot"})}}


def test_existing_run_refused_without_changing_any_evidence(
    executor: dict[str, Any], tmp_path: Path
) -> None:
    reserve = executor["reserve_run_directory"]
    output = reserve(tmp_path, "review")
    executor["write_new"](output / "receipt.json", "original receipt")
    with pytest.raises(FileExistsError):
        reserve(tmp_path, "review")
    with pytest.raises(FileExistsError):
        executor["write_new"](output / "receipt.json", "replacement")
    assert (output / "receipt.json").read_text() == "original receipt"


def test_partial_native_run_is_retained_with_failure_record(
    executor: dict[str, Any], tmp_path: Path
) -> None:
    def interrupted(_args: argparse.Namespace, output: Path) -> None:
        (output / "export.jsonl").write_text("partial native inventory")
        raise RuntimeError("native export interrupted")

    args = argparse.Namespace(output_dir=tmp_path, run_id="partial")
    with pytest.raises(RuntimeError, match="interrupted"):
        executor["run_audit"](args, interrupted)
    output = tmp_path / "runs/partial"
    assert (output / "export.jsonl").read_text() == "partial native inventory"
    assert json.loads((output / "failure.json").read_text())["error_type"] == "RuntimeError"
    with pytest.raises(FileExistsError):
        executor["run_audit"](args, interrupted)


@pytest.mark.parametrize(
    "attack", ["config", "archive", "profile", "remote", "compressed", "symlink"]
)
def test_alternative_graph_sources_fail_before_subprocess(
    executor: dict[str, Any], tmp_path: Path, attack: str
) -> None:
    artifact = source_fixture(tmp_path, executor["PINNED_IMAGE"])
    validate = executor["validate_graph_inputs"]
    validate(tmp_path, artifact)
    if attack == "config":
        (tmp_path / "valhalla.json").write_text('{"mjolnir":{"tile_dir":"/data/other"}}')
    elif attack == "archive":
        (tmp_path / "tiles.tar").write_bytes(b"alternative graph with same IDs")
    elif attack == "profile":
        (tmp_path / "walking-profile.json").write_text(
            '{"pedestrian_options":{"walking_speed":20}}'
        )
    elif attack == "remote":
        config = json.dumps(
            {
                "mjolnir": {
                    "tile_dir": "/data/tiles",
                    "tile_url": "https://example.invalid/{tilePath}",
                }
            }
        )
        (tmp_path / "valhalla.json").write_text(config)
        manifest = json.loads((tmp_path / "valhalla-build-manifest.json").read_text())
        manifest["config_sha256"] = sha(config)
        (tmp_path / "valhalla-build-manifest.json").write_text(json.dumps(manifest))
    elif attack == "compressed":
        (tmp_path / "tiles/2/000/000/001.gph.gz").write_bytes(b"alternate")
    else:
        (tmp_path / "tiles/linked").symlink_to(tmp_path / "valhalla.json")
    with pytest.raises(ValueError):
        validate(tmp_path, artifact)
