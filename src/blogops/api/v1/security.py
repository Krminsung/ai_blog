"""Tenant security, data-rights, copyright, and incident APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    Path,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission, get_principal, require_permissions
from blogops.db.session import get_session, get_tenant_session
from blogops.domain.security.enums import PrivacyRequestKind, PrivacyRequestState
from blogops.domain.security.providers import (
    ComplianceEvidenceVerifier,
    CopyrightEnforcementAdapter,
    DataDeletionWebhookVerifier,
    DataRightsExecutor,
    DataRightsPlanner,
    DataRightsPolicy,
    FailClosedSecurityAdapters,
    IncidentNotificationAdapter,
    SecurityIncidentPolicy,
    SubjectIdentityVerifier,
)
from blogops.domain.security.schemas import (
    BackupErasureEvidenceCreate,
    BackupErasureEvidenceRead,
    BreachNotificationRead,
    ComplianceAssessmentCreate,
    ComplianceAssessmentRead,
    CopyrightCaseRead,
    CopyrightCounterNoticeCreate,
    CopyrightDecision,
    CopyrightEventRead,
    CopyrightNoticeCreate,
    DeletionCertificateRead,
    LegalHoldCreate,
    LegalHoldEventRead,
    LegalHoldRead,
    LegalHoldRelease,
    PrivacyActionRead,
    PrivacyAccessEventRead,
    PrivacyConsentCreate,
    PrivacyConsentRead,
    PrivacyDownloadGrantRead,
    PrivacyExportRead,
    PrivacyRequestCreate,
    PrivacyRequestRead,
    PrivacyRequestReject,
    PrivacyScopeCreate,
    PrivacyVerificationSubmit,
    ReasonRequest,
    RetentionPolicyCreate,
    RetentionPolicyRead,
    RetentionDispositionRead,
    RetentionSweepRead,
    SecurityIncidentCreate,
    SecurityIncidentEventCreate,
    SecurityIncidentEventRead,
    SecurityIncidentNotify,
    SecurityIncidentRead,
    SubprocessorVersionCreate,
    SubprocessorVersionRead,
)
from blogops.domain.security.service import SecurityService
from blogops.domain.security.tasks import (
    enqueue_copyright_case,
    enqueue_privacy_request,
    enqueue_retention_sweep,
)

router = APIRouter(tags=["privacy", "security", "copyright"])
deletion_webhook_router = APIRouter(
    prefix="/webhooks/data-deletion", tags=["webhooks"]
)
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
UnscopedSession = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
]


Authenticated = Annotated[Principal, Depends(get_principal)]
PrivacyReader = Annotated[
    Principal, Depends(require_permissions(Permission.PRIVACY_READ))
]
PrivacyManager = Annotated[
    Principal, Depends(require_permissions(Permission.PRIVACY_MANAGE))
]
SecurityReader = Annotated[
    Principal, Depends(require_permissions(Permission.SECURITY_READ))
]
SecurityManager = Annotated[
    Principal, Depends(require_permissions(Permission.SECURITY_MANAGE))
]


def security_service(session: TenantSession) -> SecurityService:
    return SecurityService(session)


def webhook_security_service(session: UnscopedSession) -> SecurityService:
    return SecurityService(session)


def security_adapters() -> FailClosedSecurityAdapters:
    """Deployment must override with approved policy and execution adapters."""

    return FailClosedSecurityAdapters()


Service = Annotated[SecurityService, Depends(security_service)]
WebhookService = Annotated[SecurityService, Depends(webhook_security_service)]
Policy = Annotated[DataRightsPolicy, Depends(security_adapters)]
IdentityVerifier = Annotated[SubjectIdentityVerifier, Depends(security_adapters)]
Planner = Annotated[DataRightsPlanner, Depends(security_adapters)]
RightsExecutor = Annotated[DataRightsExecutor, Depends(security_adapters)]
CopyrightAdapter = Annotated[CopyrightEnforcementAdapter, Depends(security_adapters)]
IncidentNotifier = Annotated[IncidentNotificationAdapter, Depends(security_adapters)]
IncidentPolicy = Annotated[SecurityIncidentPolicy, Depends(security_adapters)]
ComplianceVerifier = Annotated[ComplianceEvidenceVerifier, Depends(security_adapters)]
DeletionWebhookVerifier = Annotated[
    DataDeletionWebhookVerifier, Depends(security_adapters)
]


async def _read_limited_deletion_webhook_body(request: Request) -> bytes:
    limit = request.app.state.settings.security_webhook_max_bytes
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length)
        except ValueError as exc:
            raise AppError(
                "DATA_DELETION_WEBHOOK_LENGTH_INVALID",
                "요청 크기가 올바르지 않습니다.",
                400,
            ) from exc
        if declared_length < 0 or declared_length > limit:
            raise AppError(
                "DATA_DELETION_WEBHOOK_TOO_LARGE",
                "플랫폼 삭제 요청 Payload가 허용 크기를 넘었습니다.",
                413,
            )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise AppError(
                "DATA_DELETION_WEBHOOK_TOO_LARGE",
                "플랫폼 삭제 요청 Payload가 허용 크기를 넘었습니다.",
                413,
            )
    return bytes(body)


@deletion_webhook_router.post(
    "/{provider}",
    response_model=PrivacyRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_provider_deletion_webhook(
    provider: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")],
    request: Request,
    service: WebhookService,
    verifier: DeletionWebhookVerifier,
    policy: Policy,
    planner: Planner,
    background_tasks: BackgroundTasks,
    response: Response,
) -> PrivacyRequestRead:
    body = await _read_limited_deletion_webhook_body(request)
    verified = await verifier.verify_deletion_webhook(
        provider=provider,
        body=body,
        headers={key.casefold(): value for key, value in request.headers.items()},
    )
    value, created = await service.accept_provider_deletion(
        provider=provider,
        body=body,
        verified=verified,
        policy=policy,
        planner=planner,
    )
    if created and value.state == PrivacyRequestState.QUEUED.value:
        background_tasks.add_task(
            enqueue_privacy_request, verified.workspace_id, value.id
        )
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return PrivacyRequestRead.model_validate(value)


@router.post(
    "/privacy/retention-policies",
    response_model=RetentionPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_retention_policy(
    data: RetentionPolicyCreate,
    principal: PrivacyManager,
    service: Service,
    policy: Policy,
) -> RetentionPolicyRead:
    return RetentionPolicyRead.model_validate(
        await service.create_retention_policy(principal, data, policy=policy)
    )


@router.get("/privacy/retention-policies", response_model=list[RetentionPolicyRead])
async def list_retention_policies(
    principal: PrivacyReader, service: Service
) -> list[RetentionPolicyRead]:
    return [
        RetentionPolicyRead.model_validate(value)
        for value in await service.list_retention_policies(principal)
    ]


@router.post(
    "/privacy/retention-sweeps",
    response_model=RetentionSweepRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_retention_sweep(
    principal: PrivacyManager,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> RetentionSweepRead:
    value, created = await service.create_retention_sweep(
        principal, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(
            enqueue_retention_sweep, principal.workspace_id, value.id
        )
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return RetentionSweepRead.model_validate(value)


@router.get("/privacy/retention-sweeps", response_model=list[RetentionSweepRead])
async def list_retention_sweeps(
    principal: PrivacyReader, service: Service
) -> list[RetentionSweepRead]:
    return [
        RetentionSweepRead.model_validate(value)
        for value in await service.list_retention_sweeps(principal)
    ]


@router.get(
    "/privacy/retention-sweeps/{sweep_id}", response_model=RetentionSweepRead
)
async def get_retention_sweep(
    sweep_id: UUID, principal: PrivacyReader, service: Service
) -> RetentionSweepRead:
    return RetentionSweepRead.model_validate(
        await service.get_retention_sweep(principal, sweep_id)
    )


@router.get(
    "/privacy/retention-sweeps/{sweep_id}/evidence",
    response_model=list[RetentionDispositionRead],
)
async def list_retention_sweep_evidence(
    sweep_id: UUID, principal: PrivacyReader, service: Service
) -> list[RetentionDispositionRead]:
    return [
        RetentionDispositionRead.model_validate(value)
        for value in await service.list_retention_disposition_evidence(
            principal, sweep_id
        )
    ]


@router.post(
    "/privacy/legal-holds",
    response_model=LegalHoldRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_legal_hold(
    data: LegalHoldCreate, principal: PrivacyManager, service: Service
) -> LegalHoldRead:
    return LegalHoldRead.model_validate(await service.create_legal_hold(principal, data))


@router.get("/privacy/legal-holds", response_model=list[LegalHoldRead])
async def list_legal_holds(
    principal: PrivacyReader, service: Service
) -> list[LegalHoldRead]:
    return [
        LegalHoldRead.model_validate(value)
        for value in await service.list_legal_holds(principal)
    ]


@router.get(
    "/privacy/legal-holds/{hold_id}/events",
    response_model=list[LegalHoldEventRead],
)
async def list_legal_hold_events(
    hold_id: UUID, principal: PrivacyReader, service: Service
) -> list[LegalHoldEventRead]:
    return [
        LegalHoldEventRead.model_validate(value)
        for value in await service.list_legal_hold_events(principal, hold_id)
    ]


@router.post("/privacy/legal-holds/{hold_id}/release", response_model=LegalHoldRead)
async def release_legal_hold(
    hold_id: UUID,
    data: LegalHoldRelease,
    principal: PrivacyManager,
    service: Service,
) -> LegalHoldRead:
    return LegalHoldRead.model_validate(
        await service.release_legal_hold(principal, hold_id, data)
    )


async def _create_rights_request(
    *,
    expected_kind: PrivacyRequestKind | None,
    data: PrivacyRequestCreate,
    principal: Principal,
    service: SecurityService,
    policy: DataRightsPolicy,
    idempotency_key: str,
    response: Response,
) -> PrivacyRequestRead:
    if expected_kind is not None and data.kind != expected_kind:
        raise AppError(
            "PRIVACY_REQUEST_KIND_MISMATCH",
            "Endpoint와 데이터 권리 요청 유형이 일치하지 않습니다.",
            422,
        )
    value, created = await service.create_privacy_request(
        principal,
        data,
        idempotency_key=idempotency_key,
        policy=policy,
    )
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return PrivacyRequestRead.model_validate(value)


@router.post(
    "/privacy/requests",
    response_model=PrivacyRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_privacy_request(
    data: PrivacyRequestCreate,
    principal: Authenticated,
    service: Service,
    policy: Policy,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> PrivacyRequestRead:
    return await _create_rights_request(
        expected_kind=None,
        data=data,
        principal=principal,
        service=service,
        policy=policy,
        idempotency_key=idempotency_key,
        response=response,
    )


@router.post(
    "/privacy/export",
    response_model=PrivacyRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_privacy_export(
    data: PrivacyScopeCreate,
    principal: Authenticated,
    service: Service,
    policy: Policy,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> PrivacyRequestRead:
    return await _create_rights_request(
        expected_kind=PrivacyRequestKind.EXPORT,
        data=PrivacyRequestCreate(
            kind=PrivacyRequestKind.EXPORT, **data.model_dump()
        ),
        principal=principal,
        service=service,
        policy=policy,
        idempotency_key=idempotency_key,
        response=response,
    )


@router.post(
    "/privacy/delete",
    response_model=PrivacyRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_privacy_deletion(
    data: PrivacyScopeCreate,
    principal: Authenticated,
    service: Service,
    policy: Policy,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> PrivacyRequestRead:
    return await _create_rights_request(
        expected_kind=PrivacyRequestKind.DELETE,
        data=PrivacyRequestCreate(
            kind=PrivacyRequestKind.DELETE, **data.model_dump()
        ),
        principal=principal,
        service=service,
        policy=policy,
        idempotency_key=idempotency_key,
        response=response,
    )


@router.post(
    "/privacy/requests/{request_id}/verify", response_model=PrivacyRequestRead
)
async def verify_privacy_request(
    request_id: UUID,
    data: PrivacyVerificationSubmit,
    principal: Authenticated,
    service: Service,
    verifier: IdentityVerifier,
    planner: Planner,
    background_tasks: BackgroundTasks,
) -> PrivacyRequestRead:
    value = await service.verify_privacy_request(
        principal,
        request_id,
        verification_token=data.verification_token.get_secret_value(),
        verifier=verifier,
    )
    if value.state == PrivacyRequestState.VERIFIED.value:
        value = await service.plan_privacy_request(principal, request_id, planner=planner)
    if value.state == PrivacyRequestState.QUEUED.value:
        background_tasks.add_task(
            enqueue_privacy_request, principal.workspace_id, value.id
        )
    return PrivacyRequestRead.model_validate(value)


@router.post(
    "/privacy/requests/{request_id}/resume", response_model=PrivacyRequestRead
)
async def resume_privacy_request(
    request_id: UUID,
    principal: PrivacyManager,
    service: Service,
    planner: Planner,
    background_tasks: BackgroundTasks,
) -> PrivacyRequestRead:
    value = await service.plan_privacy_request(principal, request_id, planner=planner)
    if value.state == PrivacyRequestState.QUEUED.value:
        background_tasks.add_task(
            enqueue_privacy_request, principal.workspace_id, value.id
        )
    return PrivacyRequestRead.model_validate(value)


@router.get("/privacy/requests", response_model=list[PrivacyRequestRead])
async def list_privacy_requests(
    principal: Authenticated, service: Service
) -> list[PrivacyRequestRead]:
    return [
        PrivacyRequestRead.model_validate(value)
        for value in await service.list_privacy_requests(principal)
    ]


@router.get("/privacy/access-events", response_model=list[PrivacyAccessEventRead])
async def list_privacy_access_events(
    principal: PrivacyManager,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PrivacyAccessEventRead]:
    return [
        PrivacyAccessEventRead.model_validate(value)
        for value in await service.list_privacy_access_events(principal, limit=limit)
    ]


@router.get("/privacy/requests/{request_id}", response_model=PrivacyRequestRead)
async def get_privacy_request(
    request_id: UUID, principal: Authenticated, service: Service
) -> PrivacyRequestRead:
    return PrivacyRequestRead.model_validate(
        await service.get_privacy_request(principal, request_id)
    )


@router.get(
    "/privacy/requests/{request_id}/actions", response_model=list[PrivacyActionRead]
)
async def list_privacy_actions(
    request_id: UUID, principal: Authenticated, service: Service
) -> list[PrivacyActionRead]:
    return [
        PrivacyActionRead.model_validate(value)
        for value in await service.list_privacy_actions(principal, request_id)
    ]


@router.post(
    "/privacy/requests/{request_id}/cancel", response_model=PrivacyRequestRead
)
async def cancel_privacy_request(
    request_id: UUID,
    data: ReasonRequest,
    principal: Authenticated,
    service: Service,
) -> PrivacyRequestRead:
    return PrivacyRequestRead.model_validate(
        await service.cancel_privacy_request(principal, request_id, reason=data.reason)
    )


@router.post(
    "/privacy/requests/{request_id}/reject", response_model=PrivacyRequestRead
)
async def reject_privacy_request(
    request_id: UUID,
    data: PrivacyRequestReject,
    principal: PrivacyManager,
    service: Service,
) -> PrivacyRequestRead:
    return PrivacyRequestRead.model_validate(
        await service.reject_privacy_request(
            principal,
            request_id,
            rejection_code=data.rejection_code,
            reason=data.reason,
        )
    )


@router.get(
    "/privacy/requests/{request_id}/export", response_model=PrivacyExportRead
)
async def get_privacy_export(
    request_id: UUID, principal: Authenticated, service: Service
) -> PrivacyExportRead:
    return PrivacyExportRead.model_validate(
        await service.get_export_artifact(principal, request_id)
    )


@router.post(
    "/privacy/requests/{request_id}/export/download",
    response_model=PrivacyDownloadGrantRead,
)
async def issue_privacy_export_download(
    request_id: UUID,
    principal: Authenticated,
    service: Service,
    executor: RightsExecutor,
) -> PrivacyDownloadGrantRead:
    value = await service.issue_export_download(
        principal, request_id, executor=executor
    )
    return PrivacyDownloadGrantRead(url=value.download_url, expires_at=value.expires_at)


@router.get(
    "/privacy/requests/{request_id}/deletion-certificate",
    response_model=DeletionCertificateRead,
)
async def get_deletion_certificate(
    request_id: UUID, principal: Authenticated, service: Service
) -> DeletionCertificateRead:
    return DeletionCertificateRead.model_validate(
        await service.get_deletion_certificate(principal, request_id)
    )


@router.post(
    "/privacy/requests/{request_id}/backup-erasure-evidence",
    response_model=BackupErasureEvidenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_backup_erasure(
    request_id: UUID,
    data: BackupErasureEvidenceCreate,
    principal: PrivacyManager,
    service: Service,
    executor: RightsExecutor,
) -> BackupErasureEvidenceRead:
    return BackupErasureEvidenceRead.model_validate(
        await service.record_backup_erasure(
            principal, request_id, data, executor=executor
        )
    )


@router.get(
    "/privacy/requests/{request_id}/backup-erasure-evidence",
    response_model=BackupErasureEvidenceRead,
)
async def get_backup_erasure_evidence(
    request_id: UUID, principal: Authenticated, service: Service
) -> BackupErasureEvidenceRead:
    return BackupErasureEvidenceRead.model_validate(
        await service.get_backup_erasure_evidence(principal, request_id)
    )


@router.post(
    "/privacy/consents",
    response_model=PrivacyConsentRead,
    status_code=status.HTTP_201_CREATED,
)
async def append_privacy_consent(
    data: PrivacyConsentCreate,
    principal: Authenticated,
    service: Service,
    idempotency_key: IdempotencyKey,
) -> PrivacyConsentRead:
    return PrivacyConsentRead.model_validate(
        await service.append_consent(
            principal, data, idempotency_key=idempotency_key
        )
    )


@router.get("/privacy/consents", response_model=list[PrivacyConsentRead])
async def list_privacy_consents(
    principal: Authenticated,
    service: Service,
    subject_id: Annotated[UUID | None, Query()] = None,
) -> list[PrivacyConsentRead]:
    return [
        PrivacyConsentRead.model_validate(value)
        for value in await service.list_consents(principal, subject_id=subject_id)
    ]


@router.post(
    "/privacy/subprocessors",
    response_model=SubprocessorVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_subprocessor_version(
    data: SubprocessorVersionCreate,
    principal: PrivacyManager,
    service: Service,
) -> SubprocessorVersionRead:
    return SubprocessorVersionRead.model_validate(
        await service.create_subprocessor_version(principal, data)
    )


@router.get("/privacy/subprocessors", response_model=list[SubprocessorVersionRead])
async def list_subprocessors(
    principal: PrivacyReader, service: Service
) -> list[SubprocessorVersionRead]:
    return [
        SubprocessorVersionRead.model_validate(value)
        for value in await service.list_subprocessors(principal)
    ]


@router.post(
    "/copyright/notices",
    response_model=CopyrightCaseRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_copyright_notice(
    data: CopyrightNoticeCreate,
    principal: Authenticated,
    service: Service,
    idempotency_key: IdempotencyKey,
    background_tasks: BackgroundTasks,
    response: Response,
) -> CopyrightCaseRead:
    value, created = await service.create_copyright_notice(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(
            enqueue_copyright_case, principal.workspace_id, value.id
        )
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return CopyrightCaseRead.model_validate(value)


@router.get("/copyright/cases/{case_id}", response_model=CopyrightCaseRead)
async def get_copyright_case(
    case_id: UUID, principal: Authenticated, service: Service
) -> CopyrightCaseRead:
    return CopyrightCaseRead.model_validate(
        await service.get_copyright_case(principal, case_id)
    )


@router.get(
    "/copyright/cases/{case_id}/events", response_model=list[CopyrightEventRead]
)
async def list_copyright_events(
    case_id: UUID, principal: Authenticated, service: Service
) -> list[CopyrightEventRead]:
    return [
        CopyrightEventRead.model_validate(value)
        for value in await service.list_copyright_events(principal, case_id)
    ]


@router.post(
    "/copyright/cases/{case_id}/counter-notice", response_model=CopyrightCaseRead
)
async def submit_copyright_counter_notice(
    case_id: UUID,
    data: CopyrightCounterNoticeCreate,
    principal: Authenticated,
    service: Service,
    adapter: CopyrightAdapter,
) -> CopyrightCaseRead:
    return CopyrightCaseRead.model_validate(
        await service.submit_counter_notice(
            principal, case_id, data, adapter=adapter
        )
    )


@router.post("/copyright/cases/{case_id}/decision", response_model=CopyrightCaseRead)
async def decide_copyright_case(
    case_id: UUID,
    data: CopyrightDecision,
    principal: SecurityManager,
    service: Service,
    adapter: CopyrightAdapter,
) -> CopyrightCaseRead:
    return CopyrightCaseRead.model_validate(
        await service.decide_copyright_case(
            principal, case_id, data, adapter=adapter
        )
    )


@router.post(
    "/security/incidents",
    response_model=SecurityIncidentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_security_incident(
    data: SecurityIncidentCreate,
    principal: SecurityManager,
    service: Service,
    policy: IncidentPolicy,
) -> SecurityIncidentRead:
    return SecurityIncidentRead.model_validate(
        await service.create_security_incident(principal, data, policy=policy)
    )


@router.get("/security/incidents", response_model=list[SecurityIncidentRead])
async def list_security_incidents(
    principal: SecurityReader, service: Service
) -> list[SecurityIncidentRead]:
    return [
        SecurityIncidentRead.model_validate(value)
        for value in await service.list_security_incidents(principal)
    ]


@router.get("/security/incidents/{incident_id}", response_model=SecurityIncidentRead)
async def get_security_incident(
    incident_id: UUID, principal: SecurityReader, service: Service
) -> SecurityIncidentRead:
    return SecurityIncidentRead.model_validate(
        await service.get_security_incident(principal, incident_id)
    )


@router.get(
    "/security/incidents/{incident_id}/events",
    response_model=list[SecurityIncidentEventRead],
)
async def list_security_incident_events(
    incident_id: UUID, principal: SecurityReader, service: Service
) -> list[SecurityIncidentEventRead]:
    return [
        SecurityIncidentEventRead.model_validate(value)
        for value in await service.list_security_incident_events(
            principal, incident_id
        )
    ]


@router.get(
    "/security/incidents/{incident_id}/notifications",
    response_model=list[BreachNotificationRead],
)
async def list_security_incident_notifications(
    incident_id: UUID, principal: SecurityReader, service: Service
) -> list[BreachNotificationRead]:
    return [
        BreachNotificationRead.model_validate(value)
        for value in await service.list_breach_notifications(principal, incident_id)
    ]


@router.post(
    "/security/incidents/{incident_id}/events", response_model=SecurityIncidentRead
)
async def append_security_incident_event(
    incident_id: UUID,
    data: SecurityIncidentEventCreate,
    principal: SecurityManager,
    service: Service,
) -> SecurityIncidentRead:
    return SecurityIncidentRead.model_validate(
        await service.append_security_incident_event(principal, incident_id, data)
    )


@router.post(
    "/security/incidents/{incident_id}/notifications",
    response_model=BreachNotificationRead,
    status_code=status.HTTP_201_CREATED,
)
async def notify_security_incident(
    incident_id: UUID,
    data: SecurityIncidentNotify,
    principal: SecurityManager,
    service: Service,
    notifier: IncidentNotifier,
) -> BreachNotificationRead:
    return BreachNotificationRead.model_validate(
        await service.notify_security_incident(
            principal, incident_id, data, notifier=notifier
        )
    )


@router.post(
    "/security/compliance-assessments",
    response_model=ComplianceAssessmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_compliance_assessment(
    data: ComplianceAssessmentCreate,
    principal: SecurityManager,
    service: Service,
    verifier: ComplianceVerifier,
) -> ComplianceAssessmentRead:
    return ComplianceAssessmentRead.model_validate(
        await service.create_compliance_assessment(
            principal, data, verifier=verifier
        )
    )


@router.get(
    "/security/compliance-assessments",
    response_model=list[ComplianceAssessmentRead],
)
async def list_compliance_assessments(
    principal: SecurityReader, service: Service
) -> list[ComplianceAssessmentRead]:
    return [
        ComplianceAssessmentRead.model_validate(value)
        for value in await service.list_compliance_assessments(principal)
    ]
