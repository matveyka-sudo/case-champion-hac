"""Fresh-process deterministic identity for all three independent analyzers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["urban_cargo", "delivery", "pipeline"])
def test_cargo_ids_are_stable_across_hash_seeds(name: str, tmp_path: Path) -> None:
    engine = Path(__file__).resolve().parents[1]
    example = engine / "examples" / f"synthetic_{name}.py"
    script = "import runpy,sys; print(runpy.run_path(sys.argv[1])['run_demo']().analysis_id)"
    outputs = set()
    for seed in ("0", "61", "random"):
        process = subprocess.run(
            [sys.executable, "-c", script, str(example)],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(engine / "src"), "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.add(process.stdout)
    assert len(outputs) == 1
