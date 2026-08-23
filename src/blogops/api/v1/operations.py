"""Platform health, incident, backup, recovery, and GA-readiness APIs."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import get_principal
from blogops.db.session import get_platform_session, get_session
from blogops.domain.operations.providers import (
    ComponentHealthProbe,
    FailClosedOperationsAdapters,
    OperationsPolicy,
    StatusNotificationAdapter,
)
from blogops.domain.operations.schemas import (
    BackupEvidenceRead,
    BackupPolicyCreate,
    BackupPolicyRead,
    BackupRunCreate,
    BackupRunRead,
    GAAssessmentCreate,
    GAAssessmentRead,
    GAGateEvidenceRead,
    HealthObservationRead,
    OperationalIncidentCreate,
    OperationalIncidentEventCreate,
    OperationalIncidentEventRead,
    OperationalIncidentNotify,
    OperationalIncidentRead,
    RecoveryEvidenceRead,
    RecoveryExerciseCreate,
    RecoveryExerciseRead,
    RunbookVersionCreate,
    RunbookVersionRead,
    ServiceComponentCreate,
    ServiceComponentRead,
    StatusNotificationRead,
)
from blogops.domain.operations.service import OperationsService
from blogops.domain.operations.tasks import (
    enqueue_backup,
    enqueue_ga_assessment,
    enqueue_recovery,
)

router = APIRouter(prefix="/operations", tags=["operations"])
status_router = APIRouter(prefix="/operations", tags=["status"])
UnscopedSession = Annotated[AsyncSession, Depends(get_session)]
PlatformSession = Annotated[AsyncSession, Depends(get_platform_session)]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
]


def require_permission_value(value: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if value not in principal.permissions:
            raise AppError("PERMISSION_DENIED", "이 작업을 수행할 권한이 없습니다.", 403)
        return principal

    return dependency


PlatformOperator = Annotated[Principal, Depends(require_permission_value("platform:operate"))]
PlatformApprover = Annotated[Principal, Depends(require_permission_value("platform:approve"))]


def operations_service(session: PlatformSession) -> OperationsService:
    return OperationsService(session)


def public_operations_service(session: UnscopedSession) -> OperationsService:
    return OperationsService(session)


def operations_adapters() -> FailClosedOperationsAdapters:
    """Deployment must override with controlled infrastructure adapters."""

    return FailClosedOperationsAdapters()


Service = Annotated[OperationsService, Depends(operations_service)]
PublicService = Annotated[OperationsService, Depends(public_operations_service)]
HealthProbe = Annotated[ComponentHealthProbe, Depends(operations_adapters)]
Policy = Annotated[OperationsPolicy, Depends(operations_adapters)]
StatusNotifier = Annotated[StatusNotificationAdapter, Depends(operations_adapters)]


@router.post(
    "/components",
    response_model=ServiceComponentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_component(
    data: ServiceComponentCreate,
    principal: PlatformOperator,
    service: Service,
) -> ServiceComponentRead:
    return ServiceComponentRead.model_validate(
        await service.create_component(principal, data)
    )


@router.get("/components", response_model=list[ServiceComponentRead])
async def list_components(
    _principal: PlatformOperator, service: Service
) -> list[ServiceComponentRead]:
    return [
        ServiceComponentRead.model_validate(value)
        for value in await service.list_components()
    ]


@router.post(
    "/components/{component_id}/probe", response_model=HealthObservationRead
)
async def probe_component(
    component_id: UUID,
    _principal: PlatformOperator,
    service: Service,
    probe: HealthProbe,
    policy: Policy,
) -> HealthObservationRead:
    return HealthObservationRead.model_validate(
        await service.probe_component(component_id, probe=probe, policy=policy)
    )


@status_router.get("/status")
async def public_status(service: PublicService) -> list[dict[str, Any]]:
    return await service.public_status()


@router.post(
    "/runbooks",
    response_model=RunbookVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_runbook(
    data: RunbookVersionCreate,
    principal: PlatformApprover,
    service: Service,
) -> RunbookVersionRead:
    return RunbookVersionRead.model_validate(
        await service.create_runbook(principal, data)
    )


@router.get("/runbooks", response_model=list[RunbookVersionRead])
async def list_runbooks(
    _principal: PlatformOperator, service: Service
) -> list[RunbookVersionRead]:
    return [
        RunbookVersionRead.model_validate(value)
        for value in await service.list_runbooks()
    ]


@router.post(
    "/backup-policies",
    response_model=BackupPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup_policy(
    data: BackupPolicyCreate,
    principal: PlatformApprover,
    service: Service,
) -> BackupPolicyRead:
    return BackupPolicyRead.model_validate(
        await service.create_backup_policy(principal, data)
    )


@router.get("/backup-policies", response_model=list[BackupPolicyRead])
async def list_backup_policies(
    _principal: PlatformOperator, service: Service
) -> list[BackupPolicyRead]:
    return [
        BackupPolicyRead.model_validate(value)
        for value in await service.list_backup_policies()
    ]


@router.post(
    "/backups",
    response_model=BackupRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_backup_run(
    data: BackupRunCreate,
    principal: PlatformOperator,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> BackupRunRead:
    value, created = await service.create_backup_run(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_backup, value.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return BackupRunRead.model_validate(value)


@router.get("/backups/{run_id}", response_model=BackupRunRead)
async def get_backup_run(
    run_id: UUID, _principal: PlatformOperator, service: Service
) -> BackupRunRead:
    return BackupRunRead.model_validate(await service.get_backup_run(run_id))


@router.get("/backups/{run_id}/evidence", response_model=BackupEvidenceRead)
async def get_backup_evidence(
    run_id: UUID, _principal: PlatformOperator, service: Service
) -> BackupEvidenceRead:
    return BackupEvidenceRead.model_validate(await service.get_backup_evidence(run_id))


@router.post(
    "/recovery-exercises",
    response_model=RecoveryExerciseRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_recovery_exercise(
    data: RecoveryExerciseCreate,
    principal: PlatformOperator,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> RecoveryExerciseRead:
    value, created = await service.create_recovery_exercise(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_recovery, value.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return RecoveryExerciseRead.model_validate(value)


@router.get(
    "/recovery-exercises/{exercise_id}", response_model=RecoveryExerciseRead
)
async def get_recovery_exercise(
    exercise_id: UUID, _principal: PlatformOperator, service: Service
) -> RecoveryExerciseRead:
    return RecoveryExerciseRead.model_validate(
        await service.get_recovery_exercise(exercise_id)
    )


@router.get(
    "/recovery-exercises/{exercise_id}/evidence",
    response_model=RecoveryEvidenceRead,
)
async def get_recovery_evidence(
    exercise_id: UUID, _principal: PlatformOperator, service: Service
) -> RecoveryEvidenceRead:
    return RecoveryEvidenceRead.model_validate(
        await service.get_recovery_evidence(exercise_id)
    )


@router.post(
    "/incidents",
    response_model=OperationalIncidentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_incident(
    data: OperationalIncidentCreate,
    principal: PlatformOperator,
    service: Service,
) -> OperationalIncidentRead:
    return OperationalIncidentRead.model_validate(
        await service.create_incident(principal, data)
    )


@router.get("/incidents", response_model=list[OperationalIncidentRead])
async def list_incidents(
    _principal: PlatformOperator, service: Service
) -> list[OperationalIncidentRead]:
    return [
        OperationalIncidentRead.model_validate(value)
        for value in await service.list_incidents()
    ]


@router.get("/incidents/{incident_id}", response_model=OperationalIncidentRead)
async def get_incident(
    incident_id: UUID, _principal: PlatformOperator, service: Service
) -> OperationalIncidentRead:
    return OperationalIncidentRead.model_validate(await service.get_incident(incident_id))


@router.get(
    "/incidents/{incident_id}/events",
    response_model=list[OperationalIncidentEventRead],
)
async def list_incident_events(
    incident_id: UUID, _principal: PlatformOperator, service: Service
) -> list[OperationalIncidentEventRead]:
    return [
        OperationalIncidentEventRead.model_validate(value)
        for value in await service.list_incident_events(incident_id)
    ]


@router.get(
    "/incidents/{incident_id}/notifications",
    response_model=list[StatusNotificationRead],
)
async def list_incident_notifications(
    incident_id: UUID, _principal: PlatformOperator, service: Service
) -> list[StatusNotificationRead]:
    return [
        StatusNotificationRead.model_validate(value)
        for value in await service.list_status_notifications(incident_id)
    ]


@router.post(
    "/incidents/{incident_id}/events", response_model=OperationalIncidentRead
)
async def append_incident_event(
    incident_id: UUID,
    data: OperationalIncidentEventCreate,
    principal: PlatformOperator,
    service: Service,
) -> OperationalIncidentRead:
    return OperationalIncidentRead.model_validate(
        await service.append_incident_event(principal, incident_id, data)
    )


@router.post(
    "/incidents/{incident_id}/notifications", status_code=status.HTTP_201_CREATED
)
async def notify_incident(
    incident_id: UUID,
    data: OperationalIncidentNotify,
    principal: PlatformOperator,
    service: Service,
    notifier: StatusNotifier,
) -> dict[str, str]:
    value = await service.notify_incident(
        principal, incident_id, data, notifier=notifier
    )
    return {
        "id": str(value.id),
        "provider_message_ref": value.provider_message_ref,
        "evidence_hash": value.evidence_hash,
    }


@router.post(
    "/ga-assessments",
    response_model=GAAssessmentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ga_assessment(
    data: GAAssessmentCreate,
    principal: PlatformApprover,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> GAAssessmentRead:
    value, created = await service.create_ga_assessment(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_ga_assessment, value.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return GAAssessmentRead.model_validate(value)


@router.get("/ga-assessments/{assessment_id}", response_model=GAAssessmentRead)
async def get_ga_assessment(
    assessment_id: UUID, _principal: PlatformOperator, service: Service
) -> GAAssessmentRead:
    return GAAssessmentRead.model_validate(
        await service.get_ga_assessment(assessment_id)
    )


@router.get(
    "/ga-assessments/{assessment_id}/evidence",
    response_model=list[GAGateEvidenceRead],
)
async def list_ga_evidence(
    assessment_id: UUID, _principal: PlatformOperator, service: Service
) -> list[GAGateEvidenceRead]:
    return [
        GAGateEvidenceRead.model_validate(value)
        for value in await service.list_ga_evidence(assessment_id)
    ]
