import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_storage import request
from test_v2_integration import request_body


@pytest.mark.asyncio
@pytest.mark.parametrize("contract", ["legacy", "2"])
async def test_separate_api_and_worker_processes_complete_a_synthetic_job(database, contract):
    _, schema, url = database
    root = Path(__file__).resolve().parents[3]
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    env = os.environ | {
        "A2_DATABASE_URL": url,
        "A2_CONTRACT_VERSION": contract,
        "A2_DATABASE_SCHEMA": schema,
        "A2_CODE_VERSION": "demo-code",
        "A2_CALCULATION_HANDLER": "worker_handlers:success"
        if contract == "legacy"
        else "v2_handlers:success",
        "A2_PREFLIGHT_HOOK": "worker_handlers:synthetic_prepare" if contract == "legacy" else "",
        "A2_REGISTRY_HOOK": "" if contract == "legacy" else "v2_handlers:registry",
        "PYTHONPATH": os.pathsep.join(
            str(root / p)
            for p in (
                "packages/contracts/src",
                "apps/api/src",
                "packages/assistant/src",
                "apps/api/tests",
            )
        ),
    }
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "a2_api.api.bootstrap:build_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    worker = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            for _ in range(100):
                try:
                    if (await client.get("/health")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.03)
            accepted = await client.post(
                "/api/v1/analyses",
                json=request().model_dump(mode="json") if contract == "legacy" else request_body(),
                headers={"Idempotency-Key": "process-smoke"},
            )
            assert accepted.status_code == 202
            job_id = accepted.json()["job_id"]
            worker = subprocess.Popen(
                [sys.executable, "-m", "a2_api.workers.cli", "worker", "--once"],
                cwd=root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert await asyncio.to_thread(worker.wait, 10) == 0
            job = (await client.get("/api/v1/jobs/" + job_id)).json()
            assert job["status"] == "succeeded"
            if contract == "legacy":
                saved = (await client.get("/api/v1/runs/" + job["run_id"])).json()
                assert saved["warnings"] == ["synthetic_example_not_city_results"]
                exported = (await client.get("/api/v1/runs/" + job["run_id"] + "/export")).json()
                assert exported["parameters"]["context"]["code_version"] == "demo-code"
            else:
                saved = (await client.get("/api/v1/analyses/" + job["analysis_id"])).json()
                assert "synthetic_only" in saved["limitations"]
                assert saved["versions"]["code_version"] == "demo-code"
                assert (
                    await client.post("/api/v1/analyses/" + job["analysis_id"] + "/explanation")
                ).json()["status"] == "template"
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            await asyncio.to_thread(worker.wait, 5)
        api.terminate()
        await asyncio.to_thread(api.wait, 5)
