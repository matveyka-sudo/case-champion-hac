"""Run only in an archived old checkout against a UUID PostgreSQL test schema."""

import asyncio
import inspect
import json
import sys
from pathlib import Path

import asyncpg
from a2_api.jobs import V2JobContext
from a2_api.storage import JobRepository
from a2_contracts.v2.results import AnalysisResult, Score
from a2_contracts.v2.scenarios import ScenarioResult, ScenarioRunRequest
from test_v2_storage import context, proposal, request, result, scenario_result


async def main():
    root, url, schema, scope, mode = sys.argv[1:]
    assert Path(inspect.getfile(JobRepository)).resolve().is_relative_to(Path(root).resolve())
    assert "calculation_kind" not in Score.model_fields
    assert schema.startswith("a2_test_") and schema.replace("_", "").isalnum()
    pool = await asyncpg.create_pool(url, server_settings={"search_path": schema})
    try:
        repository = JobRepository(pool)
        kind = "urban_passenger" if mode == "urban" else "intercity_passenger"
        if mode == "urban":
            full = result(kind, "historical-baseline")
        else:
            raw = json.loads(
                (
                    Path(root)
                    / "packages/contracts/fixtures/v2/required-dependencies-synthetic.json"
                ).read_text()
            )["payload"]
            raw["analysis_id"] = "historical-baseline"
            full = AnalysisResult.model_validate(raw)
        command = proposal(full)
        candidate = scenario_result(full, command).model_dump(mode="json")
        # No numeric effect: the original values remain mathematically consistent.
        candidate["after"]["metrics"][0]["value"] = candidate["before"]["metrics"][0]["value"]
        candidate["deltas"][0].update(delta=0.0, outcome="unchanged")
        if mode != "urban":
            payload = full.model_dump(mode="json")
            payload["references"][0]["completeness"] = "partial"
            payload["status"] = "partial"
            for score in payload["scores"]:
                score.update(completeness="partial", ranking_eligible=False)
            baseline = AnalysisResult.model_validate(payload)
        else:
            baseline = full
        await repository.enqueue(
            "analysis",
            request(kind),
            context(kind, analysis_id=baseline.analysis_id, scope=scope),
            "baseline",
            "trace",
            scope=scope,
        )
        await repository.succeed(
            await repository.claim("baseline-claim"), baseline, proposals=[proposal(baseline)]
        )
        saved = await repository.get_run(baseline.analysis_id, scope=scope)
        command = (await repository.get_proposals(saved.analysis_id, scope))[0]
        parameters = await repository.get_run_parameters(saved.analysis_id, scope=scope)
        job_context = V2JobContext.model_validate(parameters["context"]).model_copy(
            update={"scenario_proposal": command}
        )
        candidate.update(
            scenario_id=command.scenario_id,
            baseline_result_hash=saved.result_hash,
            status="partial" if mode != "urban" else "available",
        )
        candidate["before"] = {
            "metrics": [item.model_dump(mode="json") for item in saved.metrics],
            "scores": [item.model_dump(mode="json") for item in saved.scores],
        }
        if mode == "partial":
            candidate["after"]["scores"] = candidate["before"]["scores"]
        elif mode == "invalid":
            for score in candidate["after"]["scores"]:
                candidate["deltas"].append(
                    dict(
                        effect_id="status-" + score["score_id"],
                        kind="score",
                        before_id=score["score_id"],
                        after_id=score["score_id"],
                        unit="points_0_100",
                        delta=None,
                        sign_convention="after_minus_before",
                        outcome="unknown",
                        reason_code="CHANGED_COMPLETENESS",
                        scope="transport",
                    )
                )
        run = ScenarioResult.model_validate(candidate)
        submitted = ScenarioRunRequest(
            schema_version="2",
            scenario_id=command.scenario_id,
            baseline_analysis_id=saved.analysis_id,
        )
        accepted = await repository.enqueue(
            "scenario", submitted, job_context, "scenario", "trace", scope=scope
        )
        await repository.succeed(await repository.claim("scenario-claim"), run)
        assert (await repository.get_job(accepted.job_id, scope=scope)).status == "succeeded"
        print(
            json.dumps(
                dict(
                    run_id=run.run_id,
                    baseline_id=saved.analysis_id,
                    job_id=accepted.job_id,
                    request=submitted.model_dump(mode="json"),
                    context=job_context.model_dump(mode="json"),
                )
            )
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
