"""Authoritative state machine from functional specification section 34."""

from enum import StrEnum

from blogops.core.errors import AppError


class JobState(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    WAITING_INPUT = "WAITING_INPUT"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    VERIFYING = "VERIFYING"
    OPTIMIZING = "OPTIMIZING"
    CREATING_MEDIA = "CREATING_MEDIA"
    QUALITY_BLOCKED = "QUALITY_BLOCKED"
    WAITING_REVIEW = "WAITING_REVIEW"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class StepState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAITING_USER = "WAITING_USER"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATES = frozenset(
    {JobState.SUCCEEDED, JobState.FINAL_FAILED, JobState.CANCELLED, JobState.EXPIRED}
)

_ACTIVE_PIPELINE = (
    JobState.VALIDATING,
    JobState.RESEARCHING,
    JobState.PLANNING,
    JobState.GENERATING,
    JobState.VERIFYING,
    JobState.OPTIMIZING,
    JobState.CREATING_MEDIA,
)

ALLOWED_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.CREATED: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.QUEUED: frozenset({JobState.VALIDATING, JobState.CANCEL_REQUESTED}),
    JobState.VALIDATING: frozenset(
        {JobState.WAITING_INPUT, JobState.RESEARCHING, JobState.FINAL_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.WAITING_INPUT: frozenset({JobState.QUEUED, JobState.CANCEL_REQUESTED, JobState.EXPIRED}),
    JobState.RESEARCHING: frozenset(
        {JobState.PLANNING, JobState.RETRYABLE_FAILED, JobState.FINAL_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.PLANNING: frozenset(
        {JobState.GENERATING, JobState.WAITING_REVIEW, JobState.RETRYABLE_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.GENERATING: frozenset(
        {JobState.VERIFYING, JobState.PARTIAL, JobState.RETRYABLE_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.VERIFYING: frozenset(
        {JobState.OPTIMIZING, JobState.QUALITY_BLOCKED, JobState.RETRYABLE_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.OPTIMIZING: frozenset(
        {JobState.CREATING_MEDIA, JobState.WAITING_REVIEW, JobState.QUALITY_BLOCKED, JobState.CANCEL_REQUESTED}
    ),
    JobState.CREATING_MEDIA: frozenset(
        {JobState.WAITING_REVIEW, JobState.PARTIAL, JobState.RETRYABLE_FAILED, JobState.CANCEL_REQUESTED}
    ),
    JobState.QUALITY_BLOCKED: frozenset(
        {JobState.QUEUED, JobState.WAITING_REVIEW, JobState.CANCEL_REQUESTED, JobState.EXPIRED}
    ),
    JobState.WAITING_REVIEW: frozenset(
        {JobState.APPROVED, JobState.REVISION_REQUESTED, JobState.CANCEL_REQUESTED, JobState.EXPIRED}
    ),
    JobState.REVISION_REQUESTED: frozenset({JobState.QUEUED, JobState.CANCEL_REQUESTED, JobState.EXPIRED}),
    JobState.APPROVED: frozenset({JobState.SCHEDULED, JobState.PUBLISHING, JobState.SUCCEEDED}),
    JobState.SCHEDULED: frozenset({JobState.PUBLISHING, JobState.CANCEL_REQUESTED, JobState.EXPIRED}),
    JobState.PUBLISHING: frozenset(
        {JobState.SUCCEEDED, JobState.PARTIAL, JobState.RETRYABLE_FAILED, JobState.FINAL_FAILED}
    ),
    JobState.PARTIAL: frozenset({JobState.QUEUED, JobState.WAITING_REVIEW, JobState.FINAL_FAILED}),
    JobState.RETRYABLE_FAILED: frozenset({JobState.QUEUED, JobState.FINAL_FAILED, JobState.CANCEL_REQUESTED}),
    JobState.CANCEL_REQUESTED: frozenset({JobState.CANCELLED, JobState.PARTIAL}),
    JobState.SUCCEEDED: frozenset(),
    JobState.FINAL_FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.EXPIRED: frozenset(),
}


def ensure_job_transition(current: JobState, target: JobState) -> None:
    if target not in ALLOWED_JOB_TRANSITIONS[current]:
        raise AppError(
            code="INVALID_JOB_TRANSITION",
            message=f"작업 상태를 {current.value}에서 {target.value}(으)로 변경할 수 없습니다.",
            status_code=409,
            fields=[{"path": "state", "reason": f"{current.value}->{target.value}"}],
        )


def is_pipeline_state(state: JobState) -> bool:
    return state in _ACTIVE_PIPELINE
