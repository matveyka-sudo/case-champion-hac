"""Synthetic calculation handlers for tests only; never production fallback."""

import json
import os
import time
from pathlib import Path

from a2_api.services import DomainError
from a2_contracts.results import ProfileResult


def success(claim):
    fixture = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/success.json"
    payload = json.loads(fixture.read_text())["payload"]
    payload["run_id"] = "demo-run-" + claim.job.job_id
    payload["cities"] = payload["cities"][:1]
    return ProfileResult.model_validate(payload)


def slow(claim):
    path = os.environ.get("A2_TEST_CHILD_PID")
    if path:
        Path(path).write_text(str(os.getpid()))
    time.sleep(30)
    return success(claim)


def domain_failure(claim):
    raise DomainError(503, "engine_unavailable", "Synthetic engine unavailable", retryable=True)


def crash(claim):
    raise RuntimeError("secret_test_database_password")


def abrupt_exit(claim):
    os._exit(7)


def scenario_success(claim):
    from test_scenario_jobs import comparison

    result = comparison(claim).model_dump(mode="json")
    result["comparisons"][0].update(
        before=[50, 50],
        after=[50, 100],
        lower_bound_change=0,
        upper_bound_change=50,
        delta_interval_conservative=[0, 50],
    )
    return type(comparison(claim)).model_validate(result)


async def synthetic_prepare(kind, payload):
    from test_scenario_jobs import scenario_context
    from test_storage import context

    if kind == "scenario":
        return scenario_context(payload.base_run_ids["regular"])
    return context()
