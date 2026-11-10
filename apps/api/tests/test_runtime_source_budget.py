"""TEST_ONLY explicit bounded source policy and original four-profile inputs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from a2_api.adapters import walk_input_inventory
from a2_api.core.config import RuntimeSettings
from a2_api.core.runtime_budget import (
    DEFAULT_RUNTIME_SOURCE_BYTES,
    MAX_RUNTIME_SOURCE_BYTES,
    runtime_source_budget_bytes,
)
from pydantic import ValidationError


def test_source_policy_defaults_to_96_and_explicitly_accepts_128(monkeypatch):
    monkeypatch.delenv("A2_RUNTIME_SOURCE_BUDGET_MIB", raising=False)
    assert RuntimeSettings(_env_file=None).runtime_source_budget_mib == 96
    assert runtime_source_budget_bytes() == DEFAULT_RUNTIME_SOURCE_BYTES == 96 * 1024 * 1024
    assert walk_input_inventory._Reader(1).remaining == DEFAULT_RUNTIME_SOURCE_BYTES
    monkeypatch.setenv("A2_RUNTIME_SOURCE_BUDGET_MIB", "128")
    assert RuntimeSettings(_env_file=None).runtime_source_budget_mib == 128
    assert runtime_source_budget_bytes() == MAX_RUNTIME_SOURCE_BYTES == 128 * 1024 * 1024
    assert walk_input_inventory._Reader(1).remaining == MAX_RUNTIME_SOURCE_BYTES


@pytest.mark.parametrize("value", ["0", "95", "129", "192", "128.5", "NaN", "invalid"])
def test_invalid_source_policy_fails_closed_before_reads(monkeypatch, value):
    monkeypatch.setenv("A2_RUNTIME_SOURCE_BUDGET_MIB", value)
    with pytest.raises(ValidationError):
        RuntimeSettings(_env_file=None)
    with pytest.raises(ValidationError):
        runtime_source_budget_bytes()
    with pytest.raises(ValidationError):
        walk_input_inventory._Reader(1)


@pytest.mark.parametrize("budget_mib", [96, 128])
def test_original_four_profile_registration_needs_explicit_bounded_policy(budget_mib):
    runtime = Path(
        os.environ.get(
            "A2_TEST_FOUR_PROFILE_RUNTIME",
            "/Users/kirito/waydex-full-runtime/fourprofile-source-composition-cbfa33-20261111-v2",
        )
    )
    data = Path(
        os.environ.get(
            "A2_TEST_FOUR_PROFILE_DATA_DIR",
            "/Users/kirito/waydex-full-runtime/releases/cbfa33c6465b3b2fe25598eade3f605c165d3dfc/data",
        )
    )
    if not (runtime / "runtime-settings.json").is_file() or not data.is_dir():
        pytest.skip("Restore the hash-pinned four-profile runtime and its immutable data root")
    # Run each policy in a fresh process. All real file/hash/provenance checks
    # execute unchanged; only the explicit operator setting differs.
    source = """
import hashlib,json,os,resource,socket,sys,time
from pathlib import Path
root=Path(sys.argv[1]); before=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
settings=json.loads((root/'runtime-settings.json').read_bytes())
settings.update(A2_PRODUCT_RUNTIME_PATH=str(root/'manifest.json'),A2_CERTIFIED_WALK_MODEL_PATH=str(root/'kaliningrad/proof-binding.json'),A2_WALK_OBSERVATION_DIR=str(root/'observations'),A2_DATA_DIR=sys.argv[2],A2_RUNTIME_SOURCE_BUDGET_MIB=sys.argv[3])
os.environ.update(settings)
def no_network(*args,**kwargs): raise AssertionError('NETWORK_FORBIDDEN')
socket.socket.connect=no_network
from a2_api.adapters.product_runtime import load_runtime
started=time.perf_counter(); loaded=load_runtime()
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if sys.platform!='darwin': peak*=1024
assert hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()==before
print(json.dumps({'profiles':sorted(loaded.profiles),'failures':loaded.profile_failures,'scenario_documents':sum(len(rows) for rows in loaded.scenario_documents.values()),'snapshots':len(loaded.snapshots),'peak_RSS_bytes':peak,'elapsed_seconds':time.perf_counter()-started,'HTTP_calls':0,'manifest_unchanged':True}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source, str(runtime), str(data), str(budget_mib)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
    )
    receipt = json.loads(completed.stdout)
    assert receipt["snapshots"] == 4 and receipt["manifest_unchanged"]
    assert receipt["HTTP_calls"] == 0
    if budget_mib == 96:
        assert len(receipt["profiles"]) == 1
        assert receipt["failures"] == {
            "moscow-real-adult-clinic-walk-1401-threecity-v1": "RUNTIME_PROFILE_INPUTS_UNAVAILABLE",
            "novorossiysk-real-school17-walk-v1": "RUNTIME_CUMULATIVE_BYTE_BUDGET_EXCEEDED",
            "moscow-real-adult-clinic-transit-nov11-v1": "RUNTIME_CUMULATIVE_BYTE_BUDGET_EXCEEDED",
        }
    else:
        assert len(receipt["profiles"]) == 4 and not receipt["failures"]
        assert receipt["scenario_documents"] == 1
        assert receipt["peak_RSS_bytes"] < 2 * 1024 * 1024 * 1024
