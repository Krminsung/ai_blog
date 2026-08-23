"""Tenant-filtered persistence helpers for quality and approval aggregates."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from blogops.core.errors import AppError
from blogops.domain.quality.models import (
    ApprovalRequest,
    DuplicationReport,
    FactCitationReport,
    MorphologyReport,
    NaturalnessReport,
    PolicyEvent,
    PolicyOverride,
    QualityAssessment,
    QualityComment,
    QualityReport,
    QualityRuleSet,
    SafetyPolicyReport,
    SEOReport,
    WorkspaceQualityConfig,
)


class QualityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rule_set(self, workspace_id: UUID, rule_set_id: UUID) -> QualityRuleSet:
        value = await self.session.scalar(
            select(QualityRuleSet).where(
                QualityRuleSet.workspace_id == workspace_id,
                QualityRuleSet.id == rule_set_id,
            )
        )
        if value is None:
            raise _not_found("QUALITY_RULE_SET", "품질 규칙 세트")
        return value

    async def config(self, workspace_id: UUID, config_id: UUID) -> WorkspaceQualityConfig:
        value = await self.session.scalar(
            select(WorkspaceQualityConfig).where(
                WorkspaceQualityConfig.workspace_id == workspace_id,
                WorkspaceQualityConfig.id == config_id,
            )
        )
        if value is None:
            raise _not_found("QUALITY_CONFIG", "워크스페이스 품질 설정")
        return value

    async def report(self, workspace_id: UUID, report_id: UUID) -> QualityReport:
        value = await self.session.scalar(
            select(QualityReport).where(
                QualityReport.workspace_id == workspace_id,
                QualityReport.id == report_id,
            )
        )
        if value is None:
            raise _not_found("QUALITY_REPORT", "품질 보고서")
        return value

    async def report_detail(self, report: QualityReport) -> object:
        model = {
            "MORPHOLOGY": MorphologyReport,
            "NATURALNESS": NaturalnessReport,
            "SEO": SEOReport,
            "DUPLICATION": DuplicationReport,
            "FACT_CITATION": FactCitationReport,
            "SAFETY_POLICY": SafetyPolicyReport,
        }[report.report_kind]
        value = await self.session.scalar(
            select(model).where(
                model.workspace_id == report.workspace_id,
                model.report_id == report.id,
            )
        )
        if value is None:
            raise AppError(
                code="QUALITY_REPORT_DETAIL_MISSING",
                message="품질 보고서 상세 결과를 찾을 수 없습니다.",
                status_code=409,
            )
        return value

    async def assessment(
        self, workspace_id: UUID, assessment_id: UUID
    ) -> QualityAssessment:
        value = await self.session.scalar(
            select(QualityAssessment).where(
                QualityAssessment.workspace_id == workspace_id,
                QualityAssessment.id == assessment_id,
            )
        )
        if value is None:
            raise _not_found("QUALITY_ASSESSMENT", "품질 평가")
        return value

    async def policy_event(self, workspace_id: UUID, event_id: UUID) -> PolicyEvent:
        value = await self.session.scalar(
            select(PolicyEvent).where(
                PolicyEvent.workspace_id == workspace_id,
                PolicyEvent.id == event_id,
            )
        )
        if value is None:
            raise _not_found("POLICY_EVENT", "정책 이벤트")
        return value

    async def override_for_event(
        self, workspace_id: UUID, event_id: UUID
    ) -> PolicyOverride | None:
        return await self.session.scalar(
            select(PolicyOverride).where(
                PolicyOverride.workspace_id == workspace_id,
                PolicyOverride.policy_event_id == event_id,
            )
        )

    async def approval_request(
        self, workspace_id: UUID, request_id: UUID, *, for_update: bool = False
    ) -> ApprovalRequest:
        query = select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.id == request_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("APPROVAL_REQUEST", "승인 요청")
        return value

    async def comment(
        self, workspace_id: UUID, comment_id: UUID, *, for_update: bool = False
    ) -> QualityComment:
        query = select(QualityComment).where(
            QualityComment.workspace_id == workspace_id,
            QualityComment.id == comment_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("QUALITY_COMMENT", "협업 코멘트")
        return value

    async def flush(self, resource: str) -> None:
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise AppError(
                code="OPTIMISTIC_LOCK_CONFLICT",
                message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
                status_code=409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc
        except IntegrityError as exc:
            raise AppError(
                code="QUALITY_CONFLICT",
                message="같은 보고서, 평가, 정책 버전 또는 결정이 이미 존재합니다.",
                status_code=409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc


def _not_found(code: str, label: str) -> AppError:
    return AppError(
        code=f"{code}_NOT_FOUND",
        message=f"{label}을(를) 찾을 수 없습니다.",
        status_code=404,
    )
