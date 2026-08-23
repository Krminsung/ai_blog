import pytest

from blogops.core.errors import AppError
from blogops.domain.jobs.state import JobState, ensure_job_transition


def test_expected_generation_path_is_allowed() -> None:
    path = [
        JobState.CREATED,
        JobState.QUEUED,
        JobState.VALIDATING,
        JobState.RESEARCHING,
        JobState.PLANNING,
        JobState.GENERATING,
        JobState.VERIFYING,
        JobState.OPTIMIZING,
        JobState.WAITING_REVIEW,
        JobState.APPROVED,
        JobState.SUCCEEDED,
    ]
    for current, target in zip(path[:-1], path[1:], strict=True):
        ensure_job_transition(current, target)


def test_terminal_state_cannot_be_reopened() -> None:
    with pytest.raises(AppError) as exc_info:
        ensure_job_transition(JobState.SUCCEEDED, JobState.QUEUED)

    assert exc_info.value.code == "INVALID_JOB_TRANSITION"
