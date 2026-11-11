"""Operator CLI: plan verified profiles, then enqueue serially through the public API."""

import argparse
import fcntl
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import JobAccepted, JobView
from a2_contracts.v2.requests import parse_analysis_request

from a2_api.adapters.product_runtime import load_runtime
from a2_api.standard_profiles import standard_plan

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def save(path: Path, value: dict[str, Any]) -> None:
    """Persist the session/checkpoint before a submission can leave the process."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".precompute-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checked_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != "waydex-standard-plan/1":
        raise ValueError("UNKNOWN_STANDARD_PLAN")
    seen = set()
    for item in plan["requests"]:
        payload = parse_analysis_request(item["request"]).model_dump(mode="json")
        expected = canonical_hash(
            {
                "runtime": plan["runtime_sha256"],
                "deployment_version": plan["deployment_version"],
                "request": payload,
            }
        )
        if item["key"] != expected or expected in seen or payload["traffic_mode"] != "static":
            raise ValueError("STANDARD_PLAN_IDENTITY_MISMATCH")
        seen.add(expected)


def execute(
    plan: dict[str, Any],
    client: httpx.Client,
    state_path: Path,
    *,
    max_jobs: int,
    max_seconds: float,
    poll_seconds: float = 2,
) -> dict[str, Any]:
    """One active job at a time; a deadline leaves one resumable, bounded worker job.

    A rejected preflight stops the batch. It must not create hundreds of failing
    jobs or retry unavailable providers. Re-running uses the same session/key.
    """
    if not 1 <= max_jobs <= 128 or not 0 < max_seconds <= 3600 or poll_seconds < 0:
        raise ValueError("INVALID_PRECOMPUTE_BOUNDS")
    checked_plan(plan)
    binding = canonical_hash({"plan": plan, "base_url": str(client.base_url)})
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"binding": binding, "jobs": {}, "session": None}
    )
    if state["binding"] != binding:
        raise ValueError("CHECKPOINT_PLAN_OR_SERVER_MISMATCH")
    if state["session"]:
        client.cookies.set("a2_session", state["session"])
    else:
        response = client.get("capabilities")
        response.raise_for_status()
        state["session"] = client.cookies.get("a2_session")
        if not state["session"]:
            raise ValueError("SERVER_SESSION_REQUIRED")
        save(state_path, state)
    deadline = time.monotonic() + max_seconds
    admitted = 0
    for item in plan["requests"]:
        if time.monotonic() >= deadline:
            break
        key = item["key"]
        record = state["jobs"].get(key)
        if record is None:
            if admitted >= max_jobs:
                break
            # The idempotency key survives a lost response or a local crash.
            response = client.post(
                "analyses", json=item["request"], headers={"Idempotency-Key": "standard:" + key}
            )
            response.raise_for_status()
            accepted = JobAccepted.model_validate(response.json())
            record = {"job_id": accepted.job_id, "status": accepted.status}
            state["jobs"][key] = record
            admitted += 1
            save(state_path, state)
        # Even completed checkpoints are re-read from the authoritative API.
        while time.monotonic() < deadline:
            response = client.get("jobs/" + record["job_id"])
            response.raise_for_status()
            view = JobView.model_validate(response.json())
            record.update(view.model_dump(mode="json"))
            save(state_path, state)
            if view.status in TERMINAL:
                break
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
        if record["status"] != "succeeded":
            break
    # Never print the session token or checkpoint contents.
    return {
        "planned": len(plan["requests"]),
        "admitted_this_run": admitted,
        "jobs": [
            {name: row.get(name) for name in ("job_id", "status", "analysis_id")}
            for row in state["jobs"].values()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planner = sub.add_parser("plan")
    planner.add_argument("--departure-at", required=True, type=datetime.fromisoformat)
    planner.add_argument("--deployment-version", required=True)
    planner.add_argument("--output", required=True, type=Path)
    planner.add_argument("--profile", action="append", help="Optional registered profile IDs")
    runner = sub.add_parser("run")
    runner.add_argument("--plan", required=True, type=Path)
    runner.add_argument(
        "--base-url", required=True, help="API prefix, e.g. http://127.0.0.1:5193/api/v1/"
    )
    runner.add_argument("--state", required=True, type=Path)
    runner.add_argument("--max-jobs", type=int, default=1)
    runner.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args()
    if args.command == "plan":
        plan = standard_plan(load_runtime(), args.departure_at, args.deployment_version)
        if args.profile:
            selected = set(args.profile)
            available = {p for item in plan["requests"] for p in item["profiles"]}
            if selected - available:
                raise ValueError("REQUESTED_STANDARD_PROFILE_UNAVAILABLE")
            plan["requests"] = [
                item for item in plan["requests"] if selected.intersection(item["profiles"])
            ]
        save(args.output, plan)
        print(json.dumps({"planned": len(plan["requests"]), "skipped": plan["skipped"]}))
        return
    if args.plan.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("STANDARD_PLAN_BYTE_LIMIT")
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = args.state.with_suffix(args.state.suffix + ".lock")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with httpx.Client(base_url=args.base_url.rstrip("/") + "/", timeout=35) as client:
            print(
                json.dumps(
                    execute(
                        json.loads(args.plan.read_text()),
                        client,
                        args.state,
                        max_jobs=args.max_jobs,
                        max_seconds=args.max_seconds,
                    )
                )
            )


if __name__ == "__main__":
    main()
