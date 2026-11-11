"""TEST_ONLY counts describe saved batches, never a computed accessibility result."""

from datetime import UTC, datetime

import pytest
from a2_contracts.v2.api import BatchProgress, JobView
from pydantic import ValidationError


def view(status, progress=None):
    now = datetime(2026, 11, 11, tzinfo=UTC)
    return JobView(
        job_id="TEST_ONLY",
        status=status,
        request_id="TEST_ONLY",
        created_at=now,
        started_at=None if status == "queued" else now,
        finished_at=now if status in {"succeeded", "failed", "cancelled"} else None,
        analysis_id="TEST_ONLY" if status == "succeeded" else None,
        scenario_run_id=None,
        error={
            "code": "TEST_ONLY",
            "message": "TEST_ONLY",
            "field_errors": [],
            "request_id": "TEST_ONLY",
            "retryable": False,
        }
        if status == "failed"
        else None,
        progress=progress,
    )


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled"])
def test_missing_progress_is_valid_and_omitted_for_every_job_status(status):
    assert "progress" not in view(status).model_dump(mode="json")


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("phase", ["routing", "finalizing", "published", "stopped"])
def test_progress_phase_matches_publication_state(status, phase):
    progress = BatchProgress(
        completed_batches=31 if phase in {"routing", "stopped"} else 88,
        total_batches=88,
        phase=phase,
    )
    allowed = {
        "running": {"routing", "finalizing"},
        "succeeded": {"published"},
        "failed": {"stopped"},
        "cancelled": {"stopped"},
    }
    if phase in allowed.get(status, set()):
        assert view(status, progress).progress == progress
    else:
        with pytest.raises(ValidationError):
            view(status, progress)


@pytest.mark.parametrize(
    "values",
    [
        {"completed_batches": True},
        {"completed_batches": "1"},
        {"completed_batches": -1},
        {"completed_batches": 4097},
        {"total_batches": 0},
        {"total_batches": 4097},
        {"total_batches": True},
        {"completed_batches": 2, "total_batches": 1},
        {"phase": "finalizing", "completed_batches": 0},
        {"phase": "published", "completed_batches": 0},
        {"phase": "routing", "completed_batches": 1},
    ],
)
def test_batch_counts_are_bounded_strict_and_never_fabricate_whole_job_completion(values):
    with pytest.raises(ValidationError):
        BatchProgress.model_validate(
            {"completed_batches": 0, "total_batches": 1, "phase": "routing"} | values
        )


def test_all_batches_saved_can_still_be_running_or_stopped_without_a_result():
    assert (
        view(
            "running", BatchProgress(completed_batches=88, total_batches=88, phase="finalizing")
        ).analysis_id
        is None
    )
    assert (
        view(
            "failed", BatchProgress(completed_batches=88, total_batches=88, phase="stopped")
        ).analysis_id
        is None
    )
