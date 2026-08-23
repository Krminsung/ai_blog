"""Canonical quality reports, policy gates, approvals and collaboration API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.quality.enums import (
    ApprovalRequestStatus,
    AssessmentDecision,
    CollaborationTarget,
    PolicyLayer,
    ReportKind,
)
from blogops.domain.quality.references import (
    SQLAlchemyActiveMembershipResolver,
    SQLAlchemyContentVersionResolver,
)
from blogops.domain.quality.schemas import (
    ActivityRead,
    ApprovalDecisionCreate,
    ApprovalDecisionRead,
    ApprovalDecisionResultRead,
    ApprovalInvalidationCreate,
    ApprovalProofRead,
    ApprovalRequestCreate,
    ApprovalRequestRead,
    DuplicationReportCreate,
    FactCitationReportCreate,
    MorphologyReportCreate,
    NaturalnessReportCreate,
    PolicyEventRead,
    PolicyOverrideCreate,
    PolicyOverrideRead,
    QualityAssessmentCreate,
    QualityAssessmentRead,
    QualityCommentCreate,
    QualityCommentRead,
    QualityCommentResolve,
    QualityConfigCreate,
    QualityConfigRead,
    QualityReportRead,
    RuleSetCreate,
    RuleSetRead,
    SafetyPolicyReportCreate,
    SEOReportCreate,
)
from blogops.domain.quality.service import QualityService


router = APIRouter(tags=["quality", "approvals"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
QualityReader = Annotated[
    Principal, Depends(require_permissions(Permission.CONTENT_READ))
]
QualityWriter = Annotated[
    Principal, Depends(require_permissions(Permission.CONTENT_WRITE))
]
QualityApprover = Annotated[
    Principal, Depends(require_permissions(Permission.CONTENT_APPROVE))
]
QualityManager = Annotated[
    Principal, Depends(require_permissions(Permission.WORKSPACE_MANAGE))
]


def quality_service(session: TenantSession) -> QualityService:
    return QualityService(
        session,
        contents=SQLAlchemyContentVersionResolver(session),
        memberships=SQLAlchemyActiveMembershipResolver(session),
    )


Service = Annotated[QualityService, Depends(quality_service)]


@router.post(
    "/quality/rule-sets",
    response_model=RuleSetRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule_set(
    data: RuleSetCreate, principal: QualityManager, service: Service
) -> RuleSetRead:
    return RuleSetRead.model_validate(await service.create_rule_set(principal, data))


@router.get("/quality/rule-sets", response_model=list[RuleSetRead])
async def list_rule_sets(
    principal: QualityReader,
    service: Service,
    layer: PolicyLayer | None = None,
    name: str | None = None,
) -> list[RuleSetRead]:
    items = await service.list_rule_sets(principal, layer=layer, name=name)
    return [RuleSetRead.model_validate(item) for item in items]


@router.post(
    "/quality/configurations",
    response_model=QualityConfigRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_config(
    data: QualityConfigCreate, principal: QualityManager, service: Service
) -> QualityConfigRead:
    return QualityConfigRead.model_validate(
        await service.create_quality_config(principal, data)
    )


@router.get("/quality/configurations", response_model=list[QualityConfigRead])
async def list_quality_configs(
    principal: QualityReader, service: Service
) -> list[QualityConfigRead]:
    items = await service.list_quality_configs(principal)
    return [QualityConfigRead.model_validate(item) for item in items]


@router.post(
    "/quality/reports/morphology",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_morphology_report(
    data: MorphologyReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(
        principal, kind=ReportKind.MORPHOLOGY, data=data
    )


@router.post(
    "/quality/reports/naturalness",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_naturalness_report(
    data: NaturalnessReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(
        principal, kind=ReportKind.NATURALNESS, data=data
    )


@router.post(
    "/quality/reports/seo",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_seo_report(
    data: SEOReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(principal, kind=ReportKind.SEO, data=data)


@router.post(
    "/quality/reports/duplication",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_duplication_report(
    data: DuplicationReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(
        principal, kind=ReportKind.DUPLICATION, data=data
    )


@router.post(
    "/quality/reports/fact-citations",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_fact_citation_report(
    data: FactCitationReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(
        principal, kind=ReportKind.FACT_CITATION, data=data
    )


@router.post(
    "/quality/reports/safety-policy",
    response_model=QualityReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_safety_policy_report(
    data: SafetyPolicyReportCreate, principal: QualityManager, service: Service
) -> QualityReportRead:
    return await service.create_report(
        principal, kind=ReportKind.SAFETY_POLICY, data=data
    )


@router.get("/quality/reports", response_model=list[QualityReportRead])
async def list_quality_reports(
    principal: QualityReader,
    service: Service,
    content_version_id: UUID | None = None,
    report_kind: ReportKind | None = Query(default=None, alias="kind"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[QualityReportRead]:
    return await service.list_reports(
        principal,
        content_version_id=content_version_id,
        kind=report_kind,
        limit=limit,
        offset=offset,
    )


@router.get("/quality/reports/{report_id}", response_model=QualityReportRead)
async def get_quality_report(
    report_id: UUID, principal: QualityReader, service: Service
) -> QualityReportRead:
    return await service.get_report(principal, report_id)


@router.post(
    "/quality/assessments",
    response_model=QualityAssessmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_assessment(
    data: QualityAssessmentCreate, principal: QualityWriter, service: Service
) -> QualityAssessmentRead:
    return QualityAssessmentRead.model_validate(
        await service.create_assessment(principal, data)
    )


@router.get("/quality/assessments", response_model=list[QualityAssessmentRead])
async def list_quality_assessments(
    principal: QualityReader,
    service: Service,
    content_version_id: UUID | None = None,
    decision: AssessmentDecision | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[QualityAssessmentRead]:
    items = await service.list_assessments(
        principal,
        content_version_id=content_version_id,
        decision=decision,
        limit=limit,
        offset=offset,
    )
    return [QualityAssessmentRead.model_validate(item) for item in items]


@router.get(
    "/quality/assessments/{assessment_id}", response_model=QualityAssessmentRead
)
async def get_quality_assessment(
    assessment_id: UUID, principal: QualityReader, service: Service
) -> QualityAssessmentRead:
    return QualityAssessmentRead.model_validate(
        await service.get_assessment(principal, assessment_id)
    )


@router.get("/quality/policy-events", response_model=list[PolicyEventRead])
async def list_policy_events(
    principal: QualityReader,
    service: Service,
    content_version_id: UUID | None = None,
    report_id: UUID | None = None,
    assessment_id: UUID | None = None,
) -> list[PolicyEventRead]:
    items = await service.list_policy_events(
        principal,
        content_version_id=content_version_id,
        report_id=report_id,
        assessment_id=assessment_id,
    )
    return [PolicyEventRead.model_validate(item) for item in items]


@router.post(
    "/quality/policy-events/{event_id}/overrides",
    response_model=PolicyOverrideRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy_override(
    event_id: UUID,
    data: PolicyOverrideCreate,
    principal: QualityApprover,
    service: Service,
) -> PolicyOverrideRead:
    return PolicyOverrideRead.model_validate(
        await service.create_policy_override(principal, event_id, data)
    )


@router.post(
    "/approvals",
    response_model=ApprovalRequestRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval_request(
    data: ApprovalRequestCreate, principal: QualityWriter, service: Service
) -> ApprovalRequestRead:
    return ApprovalRequestRead.model_validate(
        await service.create_approval_request(principal, data)
    )


@router.get("/approvals", response_model=list[ApprovalRequestRead])
async def list_approval_requests(
    principal: QualityReader,
    service: Service,
    content_id: UUID | None = None,
    approval_status: ApprovalRequestStatus | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApprovalRequestRead]:
    items = await service.list_approval_requests(
        principal,
        content_id=content_id,
        status=approval_status,
        limit=limit,
        offset=offset,
    )
    return [ApprovalRequestRead.model_validate(item) for item in items]


@router.get("/approvals/{request_id}", response_model=ApprovalRequestRead)
async def get_approval_request(
    request_id: UUID, principal: QualityReader, service: Service
) -> ApprovalRequestRead:
    return ApprovalRequestRead.model_validate(
        await service.get_approval_request(principal, request_id)
    )


@router.post(
    "/approvals/{request_id}/decisions", response_model=ApprovalDecisionResultRead
)
async def decide_approval_request(
    request_id: UUID,
    data: ApprovalDecisionCreate,
    principal: QualityApprover,
    service: Service,
) -> ApprovalDecisionResultRead:
    result = await service.decide_approval_request(principal, request_id, data)
    return ApprovalDecisionResultRead(
        request=ApprovalRequestRead.model_validate(result.request),
        decision=(
            ApprovalDecisionRead.model_validate(result.decision)
            if result.decision is not None
            else None
        ),
    )


@router.get(
    "/approvals/{request_id}/decisions", response_model=list[ApprovalDecisionRead]
)
async def list_approval_decisions(
    request_id: UUID, principal: QualityReader, service: Service
) -> list[ApprovalDecisionRead]:
    items = await service.list_approval_decisions(principal, request_id)
    return [ApprovalDecisionRead.model_validate(item) for item in items]


@router.get("/approvals/{request_id}/proof", response_model=ApprovalProofRead)
async def get_approval_proof(
    request_id: UUID, principal: QualityReader, service: Service
) -> ApprovalProofRead:
    return ApprovalProofRead.model_validate(
        await service.approval_proof(principal, request_id)
    )


@router.post(
    "/approvals/contents/{content_id}/invalidate",
    response_model=list[ApprovalRequestRead],
)
async def invalidate_approvals_after_edit(
    content_id: UUID,
    data: ApprovalInvalidationCreate,
    principal: QualityWriter,
    service: Service,
) -> list[ApprovalRequestRead]:
    items = await service.invalidate_approvals_after_edit(principal, content_id, data)
    return [ApprovalRequestRead.model_validate(item) for item in items]


@router.post(
    "/quality/comments",
    response_model=QualityCommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_comment(
    data: QualityCommentCreate, principal: QualityWriter, service: Service
) -> QualityCommentRead:
    return await service.create_comment(principal, data)


@router.get("/quality/comments", response_model=list[QualityCommentRead])
async def list_quality_comments(
    target_type: CollaborationTarget,
    target_id: UUID,
    principal: QualityReader,
    service: Service,
) -> list[QualityCommentRead]:
    return await service.list_comments(
        principal, target_type=target_type, target_id=target_id
    )


@router.post("/quality/comments/{comment_id}/resolve", response_model=QualityCommentRead)
async def resolve_quality_comment(
    comment_id: UUID,
    data: QualityCommentResolve,
    principal: QualityWriter,
    service: Service,
) -> QualityCommentRead:
    return await service.resolve_comment(principal, comment_id, data)


@router.get("/quality/activity", response_model=list[ActivityRead])
async def list_quality_activity(
    principal: QualityReader,
    service: Service,
    content_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActivityRead]:
    items = await service.list_activity(
        principal, content_id=content_id, limit=limit, offset=offset
    )
    return [ActivityRead.model_validate(item) for item in items]
