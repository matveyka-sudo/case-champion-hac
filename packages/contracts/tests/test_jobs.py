import pytest
from a2_contracts.api import Job, JobAccepted
from pydantic import ValidationError


def job():
    return dict(
        job_id="demo-job",
        status="queued",
        trace_id="demo-trace",
        bundle_id="demo-bundle",
        input_hash="demo-input",
        code_version="demo-code",
        created_at="2026-11-07T09:00:00Z",
        started_at=None,
        finished_at=None,
        run_id=None,
        error=None,
    )


def test_job_success_requires_run_and_completion_times():
    assert Job.model_validate(job()).status == "queued"
    with pytest.raises(ValidationError):
        Job.model_validate(job() | {"status": "succeeded"})
    assert (
        Job.model_validate(
            job()
            | {
                "status": "succeeded",
                "started_at": "2026-11-07T09:00:01Z",
                "finished_at": "2026-11-07T09:00:02Z",
                "run_id": "demo-run",
            }
        ).run_id
        == "demo-run"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"run_id": "demo-run"},
        {"status": "failed"},
        {"started_at": "2026-11-07T08:00:00Z"},
    ],
)
def test_job_inconsistent_states_are_rejected(update):
    with pytest.raises(ValidationError):
        Job.model_validate(job() | update)


def test_idempotent_replay_accepts_terminal_status():
    assert JobAccepted(job_id="demo-job", status="succeeded").status == "succeeded"
