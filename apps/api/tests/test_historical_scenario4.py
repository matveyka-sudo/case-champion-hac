"""Actual 2c193054 publication must be rechecked on every current reuse path."""

import asyncio
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from a2_api.analysis_service import AnalysisService
from a2_api.api_v2 import create_app
from a2_api.jobs import V2JobContext
from a2_api.services import DomainError
from a2_api.storage import JobRepository
from a2_contracts.v2.scenarios import ScenarioRunRequest
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def old_backend4(tmp_path_factory):
    root = tmp_path_factory.mktemp("old-backend4")
    archive = subprocess.Popen(
        ["git", "archive", "2c193054feee9ab7daa3b0c93a6e76c3bbc59e8d"],
        cwd=Path(os.environ.get("A2_TEST_HISTORICAL_REPOSITORY", ROOT)),
        stdout=subprocess.PIPE,
    )
    subprocess.run(["tar", "-x", "-C", str(root)], stdin=archive.stdout, check=True)
    assert archive.wait() == 0
    (root / "apps/api/tests/historical_scenario_writer4.py").write_bytes(
        (ROOT / "apps/api/tests/historical_scenario_writer4.py").read_bytes()
    )
    return root


async def publish_historical4(database, old_root, mode):
    _, schema, url = database
    token = "A" * 43
    owner = sha256(token.encode()).hexdigest()
    env = os.environ | {
        "PYTHONPATH": os.pathsep.join(
            str(old_root / path)
            for path in (
                "packages/contracts/src",
                "apps/api/src",
                "packages/assistant/src",
                "apps/api/tests",
            )
        )
    }
    process = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(old_root / "apps/api/tests/historical_scenario_writer4.py"),
            str(old_root),
            url,
            schema,
            owner,
            mode,
        ],
        cwd=old_root,
        env=env,
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout), owner, token


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invalid", "urban", "partial"])
async def test_actual_old_scenario_publication_reuse_is_scoped_and_immutable(
    database, old_backend4, mode
):
    pool, _, _ = database
    old, owner, token = await publish_historical4(database, old_backend4, mode)
    repository = JobRepository(pool)
    before = await pool.fetch("SELECT run_id,payload,result_hash FROM runs ORDER BY run_id")
    run_id = old["run_id"]
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": token},
    ) as api:
        response = await api.get("/api/v1/scenario-runs/" + run_id)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": "B" * 43},
    ) as other:
        assert (await other.get("/api/v1/scenario-runs/" + run_id)).status_code == 404
    submitted = ScenarioRunRequest.model_validate(old["request"])
    context = V2JobContext.model_validate(old["context"])
    count = await pool.fetchval("SELECT count(*) FROM jobs")
    try:
        repeated = await repository.enqueue(
            "scenario", submitted, context, "repeat", "trace", scope=owner
        )
        cache = repeated.status
    except DomainError as error:
        cache = error.code
    observations = dict(mode=mode, http=response.status_code, cache=cache)
    assert response.status_code == (409 if mode == "invalid" else 200), observations
    if mode == "invalid":
        assert response.json()["error"]["code"] == "stored_result_incompatible"
        assert "Traceback" not in response.text
        assert cache == "stored_result_incompatible", observations
        assert await pool.fetchval("SELECT count(*) FROM jobs") == count
    else:
        assert cache == "succeeded", observations
    assert await pool.fetch("SELECT run_id,payload,result_hash FROM runs ORDER BY run_id") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invalid", "urban", "partial"])
async def test_historical_scenario_every_reuse_path_and_concurrent_reads(
    database, old_backend4, mode
):
    pool, _, _ = database
    old, owner, _ = await publish_historical4(database, old_backend4, mode)
    repository = JobRepository(pool)
    submitted = ScenarioRunRequest.model_validate(old["request"])
    context = V2JobContext.model_validate(old["context"])
    before = await pool.fetch("SELECT run_id,payload,result_hash FROM runs ORDER BY run_id")

    async def read(scope):
        try:
            await repository.get_run(old["run_id"], scope)
            return "ok"
        except DomainError as exc:
            return exc.status_code, exc.code

    observations = await asyncio.gather(
        *(read(owner) for _ in range(8)), *(read("another-owner") for _ in range(8))
    )
    assert observations[:8] == (
        [(409, "stored_result_incompatible")] * 8 if mode == "invalid" else ["ok"] * 8
    )
    assert observations[8:] == [(404, "unknown_id")] * 8
    paths = [
        lambda: repository.replay("scenario", submitted, "scenario", scope=owner),
        lambda: repository.get_job(old["job_id"], scope=owner),
        lambda: repository.get_run_parameters(old["run_id"], scope=owner),
        lambda: repository.cancel(old["job_id"], scope=owner),
    ]
    # Also test the idempotent success path when ordinary cache TTL lookup excludes it.
    await pool.execute(
        "UPDATE jobs SET cache_valid_until=now()-interval '1 second' WHERE run_id=$1", old["run_id"]
    )
    paths.append(
        lambda: repository.enqueue("scenario", submitted, context, "scenario", "trace", scope=owner)
    )
    for path in paths:
        if mode == "invalid":
            with pytest.raises(DomainError) as exc:
                await path()
            assert exc.value.code == "stored_result_incompatible"
        else:
            assert await path() is not None
    assert await pool.fetch("SELECT run_id,payload,result_hash FROM runs ORDER BY run_id") == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "baseline_payload",
        "baseline_hash",
        "proposal",
        "proposal_hash",
        "proposal_removed_assumption",
    ],
)
async def test_historical_scenario_rejects_changed_baseline_or_command(
    database, old_backend4, change
):
    pool, _, _ = database
    old, owner, token = await publish_historical4(database, old_backend4, "urban")
    repository = JobRepository(pool)
    scenario_before = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", old["run_id"]
    )
    if change in {"proposal", "proposal_removed_assumption"}:
        value = json.loads(
            await pool.fetchval(
                "SELECT payload FROM scenario_proposals WHERE scenario_id=$1",
                old["request"]["scenario_id"],
            )
        )
        if change == "proposal_removed_assumption":
            value["assumptions"] = []
        else:
            value["fixed_parameters"]["shift_seconds"] += 1
        await pool.execute(
            "UPDATE scenario_proposals SET payload=$1::jsonb WHERE scenario_id=$2",
            json.dumps(value),
            old["request"]["scenario_id"],
        )
    elif change == "proposal_hash":
        await pool.execute(
            "UPDATE scenario_proposals SET content_hash='changed' WHERE scenario_id=$1",
            old["request"]["scenario_id"],
        )
    elif change == "baseline_hash":
        await pool.execute(
            "UPDATE runs SET result_hash='changed' WHERE run_id=$1", old["baseline_id"]
        )
    else:
        value = json.loads(
            await pool.fetchval("SELECT payload FROM runs WHERE run_id=$1", old["baseline_id"])
        )
        value["metrics"][0]["value"] += 1
        await pool.execute(
            "UPDATE runs SET payload=$1::jsonb WHERE run_id=$2",
            json.dumps(value),
            old["baseline_id"],
        )
    for _ in range(2):
        with pytest.raises(DomainError) as exc:
            await repository.get_run(old["run_id"], owner)
        assert exc.value.code == "stored_result_incompatible"
    with pytest.raises(DomainError):
        await repository.enqueue(
            "scenario",
            ScenarioRunRequest.model_validate(old["request"]),
            V2JobContext.model_validate(old["context"]),
            "retry",
            "trace",
            scope=owner,
        )
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 2
    assert (
        await pool.fetchrow("SELECT payload,result_hash FROM runs WHERE run_id=$1", old["run_id"])
        == scenario_before
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(AnalysisService(repository=repository))),
        base_url="http://test",
        cookies={"a2_session": "B" * 43},
    ) as other:
        assert (await other.get("/api/v1/scenario-runs/" + old["run_id"])).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_valid", [False, True])
async def test_publication_validates_canonical_payload_not_mutable_original(
    database, old_backend4, monkeypatch, saved_valid
):
    from contextlib import asynccontextmanager

    from a2_contracts.v2.scenarios import ScenarioResult

    pool, _, _ = database
    old, owner, _ = await publish_historical4(database, old_backend4, "partial")
    repository = JobRepository(pool)
    before = await pool.fetchrow(
        "SELECT payload,result_hash FROM runs WHERE run_id=$1", old["baseline_id"]
    )
    raw = (await repository.get_run(old["run_id"], owner)).model_dump(mode="json")
    raw["run_id"] = "canonical-race"
    if not saved_valid:
        for score in raw["after"]["scores"]:
            score.update(completeness="full", ranking_eligible=True)
            raw["deltas"].append(
                dict(
                    effect_id="completion-" + score["score_id"],
                    kind="score",
                    before_id=score["score_id"],
                    after_id=score["score_id"],
                    unit="points_0_100",
                    delta=None,
                    sign_convention="after_minus_before",
                    outcome="unknown",
                    reason_code="COMPLETENESS_CHANGED",
                    scope="transport",
                )
            )
    original = ScenarioResult.model_validate(raw)
    context = V2JobContext.model_validate(old["context"]).model_copy(
        update={"snapshot_hash": "race-input"}
    )
    accepted = await repository.enqueue(
        "scenario",
        ScenarioRunRequest.model_validate(old["request"]),
        context,
        "race",
        "trace",
        scope=owner,
    )
    assert accepted.status == "queued"
    claim = await repository.claim("race-claim")

    @asynccontextmanager
    async def publication_connection():
        # succeed has already copied/canonicalized the payload at this await boundary.
        # Change the caller's object to the opposite validity, without changing saved.
        for score in original.after.scores:
            score.completeness = "full" if saved_valid else "partial"
            score.ranking_eligible = saved_valid
        async with pool.acquire() as connection:
            yield connection

    monkeypatch.setattr(repository, "_publication_connection", publication_connection)
    if saved_valid:
        await repository.succeed(claim, original)
        assert (await repository.get_run("canonical-race", owner)).after.scores[
            0
        ].completeness == "partial"
    else:
        with pytest.raises(DomainError):
            await repository.succeed(claim, original)
        assert await pool.fetchval("SELECT count(*) FROM runs WHERE run_id='canonical-race'") == 0
    assert (
        await pool.fetchrow(
            "SELECT payload,result_hash FROM runs WHERE run_id=$1", old["baseline_id"]
        )
        == before
    )
