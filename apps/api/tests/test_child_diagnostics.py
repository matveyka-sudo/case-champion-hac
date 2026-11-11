"""Unexpected child failures log bounded code metadata, never exception values."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a2_api.core.errors import DomainError
from a2_api.core.jobs import ClaimedJob
from a2_api.workers import calculation
from a2_contracts.api import FieldError
from test_product_scenarios import make_case, with_claim
from test_v2_storage import result
from test_worker_transport import Capture

SECRET = "TEST_ONLY_PRIVATE_DATABASE_PASSWORD_AND_TOKEN"
GENERIC = {
    "error": {
        "code": "engine_unavailable",
        "message": "Calculation failed",
        "field_errors": [],
        "retryable": False,
    }
}


def claim_json() -> str:
    return with_claim(make_case())[0].model_dump_json()


def child(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[ClaimedJob], object],
    encoded_claim: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    monkeypatch.setitem(sys.modules, "TEST_ONLY_diagnostic_handler", SimpleNamespace(run=handler))
    capture = Capture()
    calculation._child(
        capture,
        "TEST_ONLY_diagnostic_handler:run",
        claim_json() if encoded_claim is None else encoded_claim,
        os.getppid(),
    )
    return json.loads(capture.value), capture.value


def diagnostic(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    records = [record for record in caplog.records if record.name == calculation.logger.name]
    assert len(records) == 1
    assert records[0].exc_info is None and records[0].exc_text is None
    assert records[0].pathname == "calculation.py"
    message = records[0].getMessage()
    assert len(message.encode()) < 4096
    assert SECRET not in caplog.text
    assert "/Users/" not in message and "/private/" not in message and "C:\\" not in message
    payload = json.loads(message)
    assert payload["event"] == "calculation_child_unexpected_exception"
    assert 0 < len(payload["stack"]) <= 12
    assert all(set(frame) == {"filename", "function", "line"} for frame in payload["stack"])
    assert all(
        len(frame["filename"]) <= 64 and len(frame["function"]) <= 64 for frame in payload["stack"]
    )
    return payload


def test_sensitive_exception_is_never_formatted_and_public_error_is_byte_exact(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class SensitiveFailure(RuntimeError):
        def __str__(self) -> str:
            raise AssertionError("Exception formatting must never run")

    encoded = claim_json()
    expected = ClaimedJob.model_validate_json(encoded)

    def broken(current: ClaimedJob) -> object:
        # A handler cannot replace the validated diagnostic identity afterward.
        current.job.trace_id = SECRET
        raise SensitiveFailure("postgresql://user:" + SECRET + "@localhost/private-inputs")

    output, raw = child(monkeypatch, broken, encoded)
    assert output == GENERIC and raw == json.dumps(GENERIC).encode()
    event = diagnostic(caplog)
    assert event["exception_class"] == "SensitiveFailure"
    assert event["job_id"] == expected.job.job_id and event["trace_id"] == expected.job.trace_id
    assert event["stack_truncated"] is False


@pytest.mark.parametrize("encoded", ["not-json:" + SECRET, json.dumps({"private_input": SECRET})])
def test_failure_before_claim_validation_logs_no_unvalidated_identity(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, encoded: str
) -> None:
    output, raw = child(monkeypatch, lambda _: None, encoded)
    assert output == GENERIC and raw == json.dumps(GENERIC).encode()
    event = diagnostic(caplog)
    assert event["job_id"] is None and event["trace_id"] is None
    assert event["exception_class"] == "ValidationError"


def test_import_failure_before_claim_validation_stays_generic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failed_import(_: str) -> object:
        raise ImportError(SECRET)

    monkeypatch.setattr(calculation.importlib, "import_module", failed_import)
    capture = Capture()
    calculation._child(capture, "trusted:handler", claim_json(), os.getppid())
    assert capture.value == json.dumps(GENERIC).encode()
    event = diagnostic(caplog)
    assert event["job_id"] is None and event["trace_id"] is None
    assert event["exception_class"] == "ImportError"


def test_deep_long_location_names_and_identifiers_have_bounded_diagnostics(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    encoded = json.loads(claim_json())
    encoded["job"]["job_id"] = "j" * 10000
    encoded["job"]["trace_id"] = "/private/" + SECRET
    error_class = type("Failure" + "E" * 10000, (RuntimeError,), {})
    namespace: dict[str, Any] = {"failure": error_class(SECRET)}
    function = "handler_" + "f" * 10000
    source = (
        f"def {function}(claim):\n"
        "    def nested(depth):\n"
        "        if depth:\n"
        "            return nested(depth - 1)\n"
        "        raise failure\n"
        "    return nested(40)\n"
    )
    exec(compile(source, "/private/" + SECRET + "/" + "file" * 10000 + ".py", "exec"), namespace)
    output, raw = child(monkeypatch, namespace[function], json.dumps(encoded))
    assert output == GENERIC and raw == json.dumps(GENERIC).encode()
    event = diagnostic(caplog)
    assert event["job_id"] is None and event["trace_id"] is None
    assert len(event["exception_class"]) == 64
    assert event["stack_truncated"] is True and len(event["stack"]) == 12


def test_windows_host_paths_are_removed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    namespace: dict[str, Any] = {"failure": RuntimeError(SECRET)}
    exec(
        compile(
            "def broken(claim):\n    raise failure\n",
            "C:\\private\\" + SECRET + "\\handler.py",
            "exec",
        ),
        namespace,
    )
    child(monkeypatch, namespace["broken"])
    event = diagnostic(caplog)
    assert event["stack"][-1]["filename"] == "handler.py"


def test_expected_domain_error_payload_and_no_diagnostic_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    issue = FieldError(location=["request", "scope"], code="unknown_id", message="TEST_ONLY-safe")

    def expected(_: ClaimedJob) -> object:
        raise DomainError(
            422, "invalid_request", "TEST_ONLY-safe-domain", retryable=True, field_errors=[issue]
        )

    output, _ = child(monkeypatch, expected)
    assert output == {
        "error": {
            "code": "invalid_request",
            "message": "TEST_ONLY-safe-domain",
            "field_errors": [issue.model_dump()],
            "retryable": True,
        }
    }
    assert not [record for record in caplog.records if record.name == calculation.logger.name]


def test_success_has_no_unexpected_diagnostic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    output, _ = child(monkeypatch, lambda _: result())
    assert output["result_kind"] == "analysis_v2"
    assert not [record for record in caplog.records if record.name == calculation.logger.name]


def test_diagnostic_sink_failure_cannot_replace_the_public_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = []

    def broken(_: ClaimedJob) -> object:
        raise RuntimeError(SECRET)

    def sink_failure(*_: Any, **__: Any) -> None:
        attempted.append(True)
        raise RuntimeError("TEST_ONLY-sink-failed:" + SECRET)

    monkeypatch.setattr(calculation.logger, "handle", sink_failure)
    output, raw = child(monkeypatch, broken)
    assert output == GENERIC and raw == json.dumps(GENERIC).encode()
    assert attempted == [True]


def test_default_spawned_server_stderr_emits_only_safe_metadata() -> None:
    source = """
import json,os,sys
from types import SimpleNamespace
from a2_api.workers.calculation import _child
class Capture:
    def send_bytes(self,value): print(value.decode())
    def close(self): pass
def broken(claim):
    raise RuntimeError("TEST_ONLY_PRIVATE_DATABASE_PASSWORD_AND_TOKEN")
sys.modules['TEST_ONLY_child_log']=SimpleNamespace(run=broken)
_child(Capture(),'TEST_ONLY_child_log:run',sys.stdin.read(),os.getppid())
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        input=claim_json(),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[3],
        check=True,
        timeout=10,
    )
    assert json.loads(completed.stdout) == GENERIC
    event = json.loads(completed.stderr)
    assert event["event"] == "calculation_child_unexpected_exception"
    assert event["exception_class"] == "RuntimeError" and event["job_id"]
    assert SECRET not in completed.stderr and "/Users/" not in completed.stderr
    assert len(completed.stderr.encode()) < 4096
