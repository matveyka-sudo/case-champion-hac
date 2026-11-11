"""Environment creation never overwrites credentials or skips release checks."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "product_env", Path(__file__).with_name("product_env.py")
)
env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env)


@pytest.fixture
def prepared(tmp_path):
    args = SimpleNamespace(
        runtime=tmp_path / "runtime",
        transfer=tmp_path / "transfer",
        source=tmp_path / "source",
        output=tmp_path / "private.env",
        revision="a" * 40,
        executor_sha256="b" * 64,
        project="waydex-test",
        port=5294,
        release_pins=tmp_path / "pins.json",
        release_pins_sha256="c" * 64,
    )
    for path in (args.runtime, args.transfer, args.source):
        path.mkdir()
    for path in (
        args.runtime / "kaliningrad/proof-binding.json",
        args.runtime / "moscow/artifact.json",
        args.runtime / "kaliningrad/native/artifact.json",
        args.transfer / "bindings/novorossiysk/parent-artifact.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    manifest = {"TEST_ONLY": True}
    (args.transfer / env.transfer.MARKER).write_bytes(env.transfer.encoded(manifest))
    release = {
        "product_sha256": "d" * 64,
        "bundle_sha256": "e" * 64,
        "bindings": {"moscow": "f" * 64, "novorossiysk": "0" * 64},
    }
    with (
        patch.object(env.transfer, "load_release", return_value=release),
        patch.object(env.transfer, "verify_sources", return_value=manifest) as verify,
        patch.object(
            env.subprocess,
            "check_output",
            side_effect=lambda cmd, **kw: args.revision if "rev-parse" in cmd else "",
        ),
        patch.object(env.subprocess, "run"),
    ):
        yield args, verify


def test_private_environment_keeps_one_database_password_and_correct_shared_mounts(prepared):
    args, verify = prepared
    receipt = env.prepare(args)
    assert verify.call_count == 1
    values = dict(line.split("=", 1) for line in args.output.read_text().splitlines())
    assert len(values["POSTGRES_PASSWORD"]) == 48
    assert values["POSTGRES_PASSWORD"] not in repr(receipt)
    assert os.stat(args.output).st_mode & 0o777 == 0o600
    assert values["WAYDEX_KALININGRAD_GRAPH_DIR"] == values["WAYDEX_KALININGRAD_TRUCK_GRAPH_DIR"]
    original = args.output.read_bytes()
    with pytest.raises(ValueError, match="must be new"):
        env.prepare(args)
    assert args.output.read_bytes() == original


def test_release_verification_failure_publishes_no_environment(prepared):
    args, verify = prepared
    verify.side_effect = ValueError("TEST_ONLY unverified graph")
    with pytest.raises(ValueError, match="unverified graph"):
        env.prepare(args)
    assert not args.output.exists()


def test_environment_cannot_be_written_inside_checkout(prepared):
    args, _ = prepared
    args.output = args.source / "private.env"
    with pytest.raises(ValueError, match="outside source/data"):
        env.prepare(args)


def test_wrong_source_revision_does_not_generate_new_credentials(prepared):
    args, _ = prepared
    with patch.object(env.subprocess, "check_output", return_value="f" * 40):
        with pytest.raises(ValueError, match="revision differs"):
            env.prepare(args)
    assert not args.output.exists()
