"""Application service for immutable quality evidence and exact-version approvals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.quality.enums import (
    ActivityKind,
    ApprovalDecisionKind,
    ApprovalRequestStatus,
    AssessmentDecision,
    CollaborationTarget,
    FindingSeverity,
    PolicyAction,
    PolicyLayer,
    ReportKind,
)
from blogops.domain.quality.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStateEvent,
    DuplicationReport,
    FactCitationReport,
    MorphologyReport,
    NaturalnessReport,
    PolicyEvent,
    PolicyOverride,
    QualityActivity,
    QualityAssessment,
    QualityAssessmentReport,
    QualityComment,
    QualityMention,
    QualityReport,
    QualityRuleSet,
    SafetyPolicyReport,
    SEOReport,
    WorkspaceQualityConfig,
)
from blogops.domain.quality.references import (
    ActiveMembershipResolver,
    ContentVersionResolver,
    ContentVersionSnapshot,
)
from blogops.domain.quality.repository import QualityRepository
from blogops.domain.quality.rules import (
    NON_OVERRIDEABLE_HARD_BLOCK_LAYERS,
    POLICY_LAYER_PRIORITY,
    QUALITY_COMPONENT_WEIGHTS,
    InvalidApprovalTransition,
    PolicyFinding,
    approval_quorum_reached,
    calculate_quality_score,
    canonical_json_hash,
    evaluate_quality_gate,
    exact_content_version_matches,
    resolve_policy_findings,
    transition_approval,
)
from blogops.domain.quality.schemas import (
    ApprovalDecisionCreate,
    ApprovalInvalidationCreate,
    ApprovalRequestCreate,
    ApprovalStageConfig,
    BaseReportCreate,
    DuplicationReportCreate,
    FactCitationReportCreate,
    MorphologyReportCreate,
    NaturalnessReportCreate,
    PolicyFindingInput,
    PolicyOverrideCreate,
    QualityAssessmentCreate,
    QualityCommentCreate,
    QualityCommentRead,
    QualityCommentResolve,
    QualityConfigCreate,
    QualityReportRead,
    RuleSetCreate,
    SafetyPolicyReportCreate,
    SEOReportCreate,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"
ReportCreate = (
    MorphologyReportCreate
    | NaturalnessReportCreate
    | SEOReportCreate
    | DuplicationReportCreate
    | FactCitationReportCreate
    | SafetyPolicyReportCreate
)


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    request: ApprovalRequest
    decision: ApprovalDecision | None


class QualityService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        contents: ContentVersionResolver,
        memberships: ActiveMembershipResolver,
    ) -> None:
        self.session = session
        self.repo = QualityRepository(session)
        self.contents = contents
        self.memberships = memberships

    async def create_rule_set(
        self, principal: Principal, data: RuleSetCreate
    ) -> QualityRuleSet:
        await self._scope(principal.workspace_id)
        latest = int(
            await self.session.scalar(
                select(func.coalesce(func.max(QualityRuleSet.version), 0)).where(
                    QualityRuleSet.workspace_id == principal.workspace_id,
                    QualityRuleSet.layer == data.layer.value,
                    QualityRuleSet.name == data.name,
                )
            )
            or 0
        )
        _assert_expected_version("quality_rule_set", data.expected_previous_version, latest)
        normalized_rules = [
            _normalized_rule(data.layer, item.model_dump(mode="json"))
            for item in data.rules
        ]
        snapshot = {
            "layer": data.layer.value,
            "name": data.name,
            "version": latest + 1,
            "rules": normalized_rules,
            "analyzer_requirements": data.analyzer_requirements,
            "effective_at": data.effective_at.isoformat(),
        }
        rule_set = QualityRuleSet(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            layer=data.layer.value,
            name=data.name,
            version=latest + 1,
            rules_json=normalized_rules,
            analyzer_requirements_json=data.analyzer_requirements,
            snapshot_hash=canonical_json_hash(snapshot),
            effective_at=data.effective_at.astimezone(UTC),
            created_by=principal.subject_id,
        )
        self.session.add(rule_set)
        await self.repo.flush("quality_rule_set")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.RULE_SET_CREATED,
            action="quality.rule_set.created",
            target_type="quality_rule_set",
            target_id=rule_set.id,
            content_id=None,
            content_version_id=None,
            details={
                "layer": rule_set.layer,
                "name": rule_set.name,
                "version": rule_set.version,
                "snapshot_hash": rule_set.snapshot_hash,
            },
        )
        return rule_set

    async def list_rule_sets(
        self,
        principal: Principal,
        *,
        layer: PolicyLayer | None,
        name: str | None,
    ) -> list[QualityRuleSet]:
        query = select(QualityRuleSet).where(
            QualityRuleSet.workspace_id == principal.workspace_id
        )
        if layer is not None:
            query = query.where(QualityRuleSet.layer == layer.value)
        if name is not None:
            query = query.where(QualityRuleSet.name == name)
        return list(
            await self.session.scalars(
                query.order_by(
                    QualityRuleSet.layer,
                    QualityRuleSet.name,
                    QualityRuleSet.version.desc(),
                )
            )
        )

    async def create_quality_config(
        self, principal: Principal, data: QualityConfigCreate
    ) -> WorkspaceQualityConfig:
        await self._scope(principal.workspace_id)
        latest = int(
            await self.session.scalar(
                select(func.coalesce(func.max(WorkspaceQualityConfig.version), 0)).where(
                    WorkspaceQualityConfig.workspace_id == principal.workspace_id
                )
            )
            or 0
        )
        _assert_expected_version("workspace_quality_config", data.expected_previous_version, latest)
        approver_ids = {
            user_id
            for stage in data.approval_stages
            for user_id in stage.approver_user_ids
        }
        await self.memberships.require_active(principal.workspace_id, approver_ids)
        stages = [item.model_dump(mode="json") for item in data.approval_stages]
        snapshot = {
            "version": latest + 1,
            "minimum_total_score": str(data.minimum_total_score),
            "minimum_component_scores": {
                key: str(value) for key, value in data.minimum_component_scores.items()
            },
            "required_report_kinds": [
                item.value for item in data.required_report_kinds
            ],
            "approval_stages": stages,
            "threshold_override_allowed": data.threshold_override_allowed,
            "notes": data.notes,
        }
        config = WorkspaceQualityConfig(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            version=latest + 1,
            minimum_total_score=data.minimum_total_score,
            minimum_component_scores=snapshot["minimum_component_scores"],
            required_report_kinds=snapshot["required_report_kinds"],
            approval_stages=stages,
            threshold_override_allowed=data.threshold_override_allowed,
            notes=data.notes,
            config_hash=canonical_json_hash(snapshot),
            created_by=principal.subject_id,
        )
        self.session.add(config)
        await self.repo.flush("workspace_quality_config")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.CONFIG_CREATED,
            action="quality.config.created",
            target_type="workspace_quality_config",
            target_id=config.id,
            content_id=None,
            content_version_id=None,
            details={
                "version": config.version,
                "config_hash": config.config_hash,
            },
        )
        return config

    async def list_quality_configs(
        self, principal: Principal
    ) -> list[WorkspaceQualityConfig]:
        return list(
            await self.session.scalars(
                select(WorkspaceQualityConfig)
                .where(WorkspaceQualityConfig.workspace_id == principal.workspace_id)
                .order_by(WorkspaceQualityConfig.version.desc())
            )
        )

    async def create_report(
        self,
        principal: Principal,
        *,
        kind: ReportKind,
        data: ReportCreate,
    ) -> QualityReportRead:
        await self._scope(principal.workspace_id)
        content = await self._exact_content(
            principal.workspace_id,
            data.content_id,
            data.content_version_id,
            data.content_hash,
            require_current=False,
        )
        config = await self._latest_config(principal.workspace_id)
        rule_sets = await self._rule_sets_for_report(
            principal.workspace_id, data.rule_set_ids
        )
        self._validate_analyzer_requirements(kind, data, rule_sets)
        rule_snapshot = [_rule_set_snapshot(item) for item in rule_sets]
        config_snapshot = _quality_config_snapshot(config)
        analyzer_config_hash = canonical_json_hash(data.analyzer_config)
        rule_snapshot_hash = canonical_json_hash(rule_snapshot)
        policy_snapshot_hash = canonical_json_hash(config_snapshot)
        policy_inputs = _policy_inputs(data)
        normalized_policy_inputs = [
            _normalize_policy_input(item) for item in policy_inputs
        ]
        blockers = [
            item
            for item in normalized_policy_inputs
            if item.hard_block or item.severity is FindingSeverity.BLOCK
        ]
        report_id = uuid4()
        detail = _new_report_detail(
            principal.workspace_id, report_id, kind, data
        )
        detail_payload = _detail_payload(detail)
        report_payload = {
            "content_id": str(content.content_id),
            "content_version_id": str(content.content_version_id),
            "content_hash": content.content_hash,
            "report_kind": kind.value,
            "analyzer": data.analyzer.model_dump(mode="json"),
            "input_hash": data.input_hash,
            "analyzer_config_hash": analyzer_config_hash,
            "rule_snapshot_hash": rule_snapshot_hash,
            "policy_snapshot_hash": policy_snapshot_hash,
            "summary": data.summary,
            "findings": [item.model_dump(mode="json") for item in data.findings],
            "hard_blockers": [item.model_dump(mode="json") for item in blockers],
            "detail": detail_payload,
        }
        report = QualityReport(
            id=report_id,
            workspace_id=principal.workspace_id,
            content_id=content.content_id,
            content_version_id=content.content_version_id,
            content_hash=content.content_hash,
            report_kind=kind.value,
            analyzer_name=data.analyzer.analyzer_name,
            analyzer_version=data.analyzer.analyzer_version,
            model_name=data.analyzer.model_name,
            model_version=data.analyzer.model_version,
            dictionary_name=data.analyzer.dictionary_name,
            dictionary_version=data.analyzer.dictionary_version,
            input_hash=data.input_hash,
            analyzer_config_snapshot=data.analyzer_config,
            analyzer_config_hash=analyzer_config_hash,
            rule_snapshot=rule_snapshot,
            rule_snapshot_hash=rule_snapshot_hash,
            policy_snapshot=config_snapshot,
            policy_snapshot_hash=policy_snapshot_hash,
            summary_json=data.summary,
            findings_json=[item.model_dump(mode="json") for item in data.findings],
            hard_blockers_json=[
                item.model_dump(mode="json") for item in blockers
            ],
            report_hash=canonical_json_hash(report_payload),
            created_by=principal.subject_id,
        )
        self.session.add(report)
        self.session.add(detail)
        policy_events = [
            _new_policy_event(
                principal,
                content=content,
                report=report,
                assessment_id=None,
                finding=item,
                rule_snapshot_hash=rule_snapshot_hash,
                policy_snapshot_hash=policy_snapshot_hash,
            )
            for item in normalized_policy_inputs
        ]
        self.session.add_all(policy_events)
        await self.repo.flush("quality_report")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.REPORT_CREATED,
            action="quality.report.created",
            target_type="quality_report",
            target_id=report.id,
            content_id=report.content_id,
            content_version_id=report.content_version_id,
            details={
                "report_kind": report.report_kind,
                "report_hash": report.report_hash,
                "analyzer_version": report.analyzer_version,
                "policy_event_ids": [str(item.id) for item in policy_events],
            },
        )
        return _report_read(report, detail)

    async def get_report(
        self, principal: Principal, report_id: UUID
    ) -> QualityReportRead:
        report = await self.repo.report(principal.workspace_id, report_id)
        detail = await self.repo.report_detail(report)
        return _report_read(report, detail)

    async def list_reports(
        self,
        principal: Principal,
        *,
        content_version_id: UUID | None,
        kind: ReportKind | None,
        limit: int,
        offset: int,
    ) -> list[QualityReportRead]:
        query = select(QualityReport).where(
            QualityReport.workspace_id == principal.workspace_id
        )
        if content_version_id is not None:
            query = query.where(QualityReport.content_version_id == content_version_id)
        if kind is not None:
            query = query.where(QualityReport.report_kind == kind.value)
        reports = list(
            await self.session.scalars(
                query.order_by(QualityReport.created_at.desc(), QualityReport.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return [
            _report_read(item, await self.repo.report_detail(item)) for item in reports
        ]

    async def create_assessment(
        self, principal: Principal, data: QualityAssessmentCreate
    ) -> QualityAssessment:
        await self._scope(principal.workspace_id)
        content = await self._exact_content(
            principal.workspace_id,
            data.content_id,
            data.content_version_id,
            data.content_hash,
            require_current=False,
        )
        config = await self.repo.config(principal.workspace_id, data.quality_config_id)
        required_kinds = {ReportKind(item) for item in config.required_report_kinds}
        supplied_kinds = set(data.report_ids)
        missing = sorted(
            (item.value for item in required_kinds.difference(supplied_kinds))
        )
        if missing:
            raise AppError(
                code="QUALITY_REPORTS_REQUIRED",
                message="품질 평가에 필요한 보고서가 누락되었습니다.",
                status_code=422,
                fields=[{"path": "report_ids", "reason": item} for item in missing],
            )
        formula_required = set(ReportKind)
        missing_formula = sorted(
            item.value for item in formula_required.difference(supplied_kinds)
        )
        if missing_formula:
            raise AppError(
                code="QUALITY_FORMULA_REPORTS_REQUIRED",
                message="기본 품질 산식에 필요한 보고서가 누락되었습니다.",
                status_code=422,
                fields=[
                    {"path": "report_ids", "reason": item} for item in missing_formula
                ],
            )
        reports: dict[ReportKind, QualityReport] = {}
        details: dict[ReportKind, object] = {}
        for expected_kind, report_id in data.report_ids.items():
            report = await self.repo.report(principal.workspace_id, report_id)
            if report.report_kind != expected_kind.value:
                raise AppError(
                    code="QUALITY_REPORT_KIND_MISMATCH",
                    message="보고서 식별자와 보고서 종류가 일치하지 않습니다.",
                    status_code=422,
                    fields=[
                        {
                            "path": f"report_ids.{expected_kind.value}",
                            "reason": report.report_kind,
                        }
                    ],
                )
            _assert_report_content(report, content)
            reports[expected_kind] = report
            details[expected_kind] = await self.repo.report_detail(report)
        component_scores = _component_scores(details)
        score = calculate_quality_score(component_scores)
        report_ids = [item.id for item in reports.values()]
        policy_events = list(
            await self.session.scalars(
                select(PolicyEvent).where(
                    PolicyEvent.workspace_id == principal.workspace_id,
                    PolicyEvent.report_id.in_(report_ids),
                )
            )
        )
        overrides = list(
            await self.session.scalars(
                select(PolicyOverride).where(
                    PolicyOverride.workspace_id == principal.workspace_id,
                    PolicyOverride.policy_event_id.in_([item.id for item in policy_events]),
                )
            )
        ) if policy_events else []
        overridden_ids = {str(item.policy_event_id) for item in overrides}
        resolution = resolve_policy_findings(
            [_policy_finding_from_event(item) for item in policy_events],
            overridden_event_keys=overridden_ids,
        )
        gate = evaluate_quality_gate(
            score,
            minimum_total_score=config.minimum_total_score,
            minimum_component_scores=config.minimum_component_scores,
            policy=resolution,
        )
        config_snapshot = _quality_config_snapshot(config)
        report_manifest = [
            {
                "report_id": str(report.id),
                "report_kind": report.report_kind,
                "report_hash": report.report_hash,
                "analyzer_name": report.analyzer_name,
                "analyzer_version": report.analyzer_version,
                "rule_snapshot_hash": report.rule_snapshot_hash,
                "policy_snapshot_hash": report.policy_snapshot_hash,
            }
            for report in sorted(reports.values(), key=lambda item: item.report_kind)
        ]
        assessment_id = uuid4()
        blocking_ids = list(resolution.blocking_event_keys)
        non_overrideable_ids = list(resolution.non_overrideable_event_keys)
        threshold_event: PolicyEvent | None = None
        if gate.failed_thresholds:
            threshold_event_id = uuid4()
            threshold_finding = PolicyFindingInput(
                event_key=f"quality-threshold:{assessment_id}",
                layer=PolicyLayer.WORKSPACE,
                rule_code="QUALITY_THRESHOLD",
                action=PolicyAction.BLOCK,
                severity=FindingSeverity.BLOCK,
                hard_block=False,
                override_allowed=config.threshold_override_allowed,
                message="워크스페이스 품질 임계값을 충족하지 못했습니다.",
                evidence={"failed_thresholds": gate.failed_thresholds},
            )
            threshold_event = _new_policy_event(
                principal,
                content=content,
                report=None,
                assessment_id=assessment_id,
                finding=threshold_finding,
                rule_snapshot_hash=canonical_json_hash(
                    {"rule": "QUALITY_THRESHOLD", "config_hash": config.config_hash}
                ),
                policy_snapshot_hash=config.config_hash,
                event_id=threshold_event_id,
            )
            blocking_ids.append(str(threshold_event_id))
            if not config.threshold_override_allowed:
                non_overrideable_ids.append(str(threshold_event_id))
        assessment_payload = {
            "content_version_id": str(content.content_version_id),
            "content_hash": content.content_hash,
            "quality_config_hash": config.config_hash,
            "report_manifest": report_manifest,
            "component_scores": {
                key: str(value) for key, value in score.component_scores.items()
            },
            "component_weights": {
                key: str(value) for key, value in QUALITY_COMPONENT_WEIGHTS.items()
            },
            "weighted_contributions": {
                key: str(value)
                for key, value in score.weighted_contributions.items()
            },
            "total_score": str(score.total),
            "formula_version": score.formula_version,
            "failed_thresholds": gate.failed_thresholds,
            "blocking_policy_event_ids": blocking_ids,
            "non_overrideable_policy_event_ids": non_overrideable_ids,
            "decision": gate.decision.value,
        }
        assessment = QualityAssessment(
            id=assessment_id,
            workspace_id=principal.workspace_id,
            content_id=content.content_id,
            content_version_id=content.content_version_id,
            content_hash=content.content_hash,
            quality_config_id=config.id,
            quality_config_snapshot=config_snapshot,
            quality_config_hash=config.config_hash,
            report_manifest=report_manifest,
            component_scores=assessment_payload["component_scores"],
            component_weights=assessment_payload["component_weights"],
            weighted_contributions=assessment_payload["weighted_contributions"],
            total_score=score.total,
            formula_version=score.formula_version,
            failed_thresholds=gate.failed_thresholds,
            blocking_policy_event_ids=blocking_ids,
            non_overrideable_policy_event_ids=non_overrideable_ids,
            decision=gate.decision.value,
            assessment_hash=canonical_json_hash(assessment_payload),
            created_by=principal.subject_id,
        )
        self.session.add(assessment)
        self.session.add_all(
            [
                QualityAssessmentReport(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    assessment_id=assessment.id,
                    report_id=report.id,
                    report_kind=report.report_kind,
                    report_hash=report.report_hash,
                )
                for report in reports.values()
            ]
        )
        if threshold_event is not None:
            self.session.add(threshold_event)
        await self.repo.flush("quality_assessment")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.ASSESSMENT_CREATED,
            action="quality.assessment.created",
            target_type="quality_assessment",
            target_id=assessment.id,
            content_id=assessment.content_id,
            content_version_id=assessment.content_version_id,
            details={
                "assessment_hash": assessment.assessment_hash,
                "decision": assessment.decision,
                "total_score": str(assessment.total_score),
                "blocking_policy_event_ids": blocking_ids,
            },
        )
        return assessment

    async def get_assessment(
        self, principal: Principal, assessment_id: UUID
    ) -> QualityAssessment:
        return await self.repo.assessment(principal.workspace_id, assessment_id)

    async def list_assessments(
        self,
        principal: Principal,
        *,
        content_version_id: UUID | None,
        decision: AssessmentDecision | None,
        limit: int,
        offset: int,
    ) -> list[QualityAssessment]:
        query = select(QualityAssessment).where(
            QualityAssessment.workspace_id == principal.workspace_id
        )
        if content_version_id is not None:
            query = query.where(
                QualityAssessment.content_version_id == content_version_id
            )
        if decision is not None:
            query = query.where(QualityAssessment.decision == decision.value)
        return list(
            await self.session.scalars(
                query.order_by(QualityAssessment.created_at.desc(), QualityAssessment.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def list_policy_events(
        self,
        principal: Principal,
        *,
        content_version_id: UUID | None,
        report_id: UUID | None,
        assessment_id: UUID | None,
    ) -> list[PolicyEvent]:
        query = select(PolicyEvent).where(
            PolicyEvent.workspace_id == principal.workspace_id
        )
        if content_version_id is not None:
            query = query.where(PolicyEvent.content_version_id == content_version_id)
        if report_id is not None:
            query = query.where(PolicyEvent.report_id == report_id)
        if assessment_id is not None:
            query = query.where(PolicyEvent.assessment_id == assessment_id)
        return list(
            await self.session.scalars(
                query.order_by(PolicyEvent.priority, PolicyEvent.created_at, PolicyEvent.id)
            )
        )

    async def create_policy_override(
        self,
        principal: Principal,
        event_id: UUID,
        data: PolicyOverrideCreate,
    ) -> PolicyOverride:
        event = await self.repo.policy_event(principal.workspace_id, event_id)
        event_snapshot_hash = _policy_event_snapshot_hash(event)
        _assert_hash(
            "policy_event", data.expected_event_snapshot_hash, event_snapshot_hash
        )
        event_is_blocking = (
            event.hard_block
            or event.severity == FindingSeverity.BLOCK.value
            or event.action == PolicyAction.BLOCK.value
        )
        if (
            event_is_blocking
            and PolicyLayer(event.layer) in NON_OVERRIDEABLE_HARD_BLOCK_LAYERS
        ):
            raise _override_forbidden(event)
        if not event.override_allowed:
            raise _override_forbidden(event)
        existing = await self.repo.override_for_event(principal.workspace_id, event.id)
        if existing is not None:
            raise AppError(
                code="POLICY_EVENT_ALREADY_OVERRIDDEN",
                message="이 정책 이벤트에는 이미 유효한 예외 승인이 있습니다.",
                status_code=409,
            )
        override = PolicyOverride(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            policy_event_id=event.id,
            reason=data.reason,
            evidence_json=data.evidence,
            event_snapshot_hash=event_snapshot_hash,
            overridden_by=principal.subject_id,
        )
        self.session.add(override)
        await self.repo.flush("quality_policy_override")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.POLICY_OVERRIDE_CREATED,
            action="quality.policy.override_created",
            target_type="policy_event",
            target_id=event.id,
            content_id=event.content_id,
            content_version_id=event.content_version_id,
            details={
                "override_id": str(override.id),
                "event_snapshot_hash": event_snapshot_hash,
                "layer": event.layer,
                "rule_code": event.rule_code,
                "reason": data.reason,
            },
        )
        return override

    async def create_approval_request(
        self, principal: Principal, data: ApprovalRequestCreate
    ) -> ApprovalRequest:
        await self._scope(principal.workspace_id)
        content = await self._exact_content(
            principal.workspace_id,
            data.content_id,
            data.content_version_id,
            data.content_hash,
            require_current=True,
        )
        assessment = await self.repo.assessment(
            principal.workspace_id, data.assessment_id
        )
        _assert_hash(
            "quality_assessment",
            data.expected_assessment_hash,
            assessment.assessment_hash,
        )
        if (
            assessment.content_id != content.content_id
            or assessment.content_version_id != content.content_version_id
            or assessment.content_hash != content.content_hash
        ):
            raise AppError(
                code="APPROVAL_ASSESSMENT_CONTENT_MISMATCH",
                message="품질 평가가 승인 대상 콘텐츠 버전과 일치하지 않습니다.",
                status_code=422,
            )
        await self._assert_assessment_approval_eligible(
            principal.workspace_id, assessment
        )
        config = await self.repo.config(
            principal.workspace_id, assessment.quality_config_id
        )
        if config.config_hash != assessment.quality_config_hash:
            raise AppError(
                code="APPROVAL_CONFIG_SNAPSHOT_MISMATCH",
                message="평가의 품질 설정 스냅샷이 저장된 버전과 일치하지 않습니다.",
                status_code=409,
            )
        stages = [ApprovalStageConfig.model_validate(item) for item in config.approval_stages]
        if not stages:
            raise AppError(
                code="APPROVAL_STAGES_REQUIRED",
                message="승인 요청에는 한 개 이상의 승인 단계가 필요합니다.",
                status_code=409,
            )
        await self.memberships.require_active(
            principal.workspace_id,
            {user_id for stage in stages for user_id in stage.approver_user_ids},
        )
        superseded: ApprovalRequest | None = None
        if data.supersedes_request_id is not None:
            superseded = await self.repo.approval_request(
                principal.workspace_id,
                data.supersedes_request_id,
                for_update=True,
            )
            if superseded.content_id != content.content_id:
                raise AppError(
                    code="APPROVAL_SUPERSEDES_CONTENT_MISMATCH",
                    message="같은 콘텐츠의 승인 요청만 대체할 수 있습니다.",
                    status_code=422,
                )
            if superseded.status == ApprovalRequestStatus.SUPERSEDED.value:
                raise AppError(
                    code="APPROVAL_ALREADY_SUPERSEDED",
                    message="이미 대체된 승인 요청입니다.",
                    status_code=409,
                )
            superseded_from_status = superseded.status
            superseded.status = ApprovalRequestStatus.SUPERSEDED.value
            superseded.invalidated_at = datetime.now(UTC)
            superseded.invalidated_by = principal.subject_id
            superseded.invalidation_reason = "새 콘텐츠 버전 승인 요청으로 대체됨"
            self.session.add(
                _approval_state_event(
                    principal,
                    superseded,
                    event_type="SUPERSEDED",
                    from_status=superseded_from_status,
                    details={"replacement_content_version_id": str(content.content_version_id)},
                )
            )
        now = datetime.now(UTC)
        stage_snapshot = [item.model_dump(mode="json") for item in stages]
        request = ApprovalRequest(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content.content_id,
            content_version_id=content.content_version_id,
            content_hash=content.content_hash,
            assessment_id=assessment.id,
            assessment_hash=assessment.assessment_hash,
            quality_config_id=config.id,
            quality_config_hash=config.config_hash,
            approval_stages_snapshot=stage_snapshot,
            approval_stages_hash=canonical_json_hash(stage_snapshot),
            status=ApprovalRequestStatus.PENDING.value,
            current_stage_index=0,
            stage_due_at=_stage_due_at(now, stages[0]),
            supersedes_request_id=(superseded.id if superseded else None),
            requested_by=principal.subject_id,
            requested_at=now,
            lock_version=1,
        )
        self.session.add(request)
        self.session.add(
            _approval_state_event(
                principal,
                request,
                event_type="REQUESTED",
                from_status=None,
                details={
                    "assessment_hash": assessment.assessment_hash,
                    "approval_stages_hash": request.approval_stages_hash,
                },
            )
        )
        await self.repo.flush("approval_request")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.APPROVAL_REQUESTED,
            action="quality.approval.requested",
            target_type="approval_request",
            target_id=request.id,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            details={
                "content_hash": request.content_hash,
                "assessment_id": str(request.assessment_id),
                "assessment_hash": request.assessment_hash,
                "approval_stages_hash": request.approval_stages_hash,
                "supersedes_request_id": (
                    str(request.supersedes_request_id)
                    if request.supersedes_request_id
                    else None
                ),
            },
        )
        return request

    async def get_approval_request(
        self, principal: Principal, request_id: UUID
    ) -> ApprovalRequest:
        return await self.repo.approval_request(principal.workspace_id, request_id)

    async def list_approval_requests(
        self,
        principal: Principal,
        *,
        content_id: UUID | None,
        status: ApprovalRequestStatus | None,
        limit: int,
        offset: int,
    ) -> list[ApprovalRequest]:
        query = select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == principal.workspace_id
        )
        if content_id is not None:
            query = query.where(ApprovalRequest.content_id == content_id)
        if status is not None:
            query = query.where(ApprovalRequest.status == status.value)
        return list(
            await self.session.scalars(
                query.order_by(ApprovalRequest.requested_at.desc(), ApprovalRequest.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def decide_approval_request(
        self,
        principal: Principal,
        request_id: UUID,
        data: ApprovalDecisionCreate,
    ) -> ApprovalDecisionResult:
        request = await self.repo.approval_request(
            principal.workspace_id, request_id, for_update=True
        )
        _assert_lock("approval_request", data.expected_lock_version, request.lock_version)
        if (
            request.content_version_id != data.expected_content_version_id
            or request.content_hash != data.expected_content_hash
        ):
            raise AppError(
                code="APPROVAL_CONTENT_SNAPSHOT_CONFLICT",
                message="승인 요청의 콘텐츠 버전 또는 해시가 변경되었습니다.",
                status_code=409,
            )
        if request.status != ApprovalRequestStatus.PENDING.value:
            raise AppError(
                code="APPROVAL_REQUEST_NOT_PENDING",
                message="승인 대기 중인 요청에만 결정을 기록할 수 있습니다.",
                status_code=409,
            )
        current = await self.contents.current(
            principal.workspace_id, request.content_id
        )
        if not exact_content_version_matches(
            expected_version_id=request.content_version_id,
            expected_content_hash=request.content_hash,
            actual_version_id=current.content_version_id,
            actual_content_hash=current.content_hash,
        ):
            await self._invalidate_request(
                principal,
                request,
                reason="콘텐츠 편집으로 승인 대상 버전 또는 해시가 변경됨",
                new_version_id=current.content_version_id,
                new_content_hash=current.content_hash,
            )
            await self.repo.flush("approval_invalidation")
            return ApprovalDecisionResult(request=request, decision=None)
        now = datetime.now(UTC)
        if request.stage_due_at is not None and now > request.stage_due_at:
            old_status = request.status
            request.status = ApprovalRequestStatus.EXPIRED.value
            self.session.add(
                _approval_state_event(
                    principal,
                    request,
                    event_type="EXPIRED",
                    from_status=old_status,
                    details={"stage_due_at": request.stage_due_at.isoformat()},
                )
            )
            await self.repo.flush("approval_expiration")
            await self._record_change(
                principal,
                activity_kind=ActivityKind.APPROVAL_DECIDED,
                action="quality.approval.expired",
                target_type="approval_request",
                target_id=request.id,
                content_id=request.content_id,
                content_version_id=request.content_version_id,
                details={"stage_due_at": request.stage_due_at.isoformat()},
            )
            return ApprovalDecisionResult(request=request, decision=None)
        stages = [
            ApprovalStageConfig.model_validate(item)
            for item in request.approval_stages_snapshot
        ]
        if request.current_stage_index >= len(stages):
            raise AppError(
                code="APPROVAL_STAGE_INVALID",
                message="현재 승인 단계 스냅샷이 올바르지 않습니다.",
                status_code=409,
            )
        stage = stages[request.current_stage_index]
        if stage.approver_user_ids and principal.subject_id not in stage.approver_user_ids:
            raise AppError(
                code="APPROVER_NOT_ASSIGNED",
                message="현재 단계의 승인자로 지정되지 않았습니다.",
                status_code=403,
            )
        authentication_methods = set(principal.authentication_method.split("+"))
        if stage.require_mfa and authentication_methods.isdisjoint(
            {"mfa", "totp", "totp_or_recovery", "webauthn"}
        ):
            raise AppError(
                code="APPROVAL_MFA_REQUIRED",
                message="현재 승인 단계에는 다중 인증이 필요합니다.",
                status_code=403,
            )
        prior_actors = set(
            await self.session.scalars(
                select(ApprovalDecision.decided_by).where(
                    ApprovalDecision.workspace_id == principal.workspace_id,
                    ApprovalDecision.approval_request_id == request.id,
                    ApprovalDecision.stage_key == stage.key,
                    ApprovalDecision.decision == ApprovalDecisionKind.APPROVE.value,
                )
            )
        )
        if principal.subject_id in prior_actors:
            raise AppError(
                code="APPROVAL_DECISION_DUPLICATE",
                message="현재 단계에 이미 결정을 기록했습니다.",
                status_code=409,
            )
        quorum = approval_quorum_reached(
            prior_actors, principal.subject_id, stage.required_approvals
        )
        final_stage = request.current_stage_index == len(stages) - 1
        old_status = ApprovalRequestStatus(request.status)
        try:
            new_status = transition_approval(
                old_status,
                data.decision,
                stage_quorum_reached=quorum,
                final_stage=final_stage,
            )
        except InvalidApprovalTransition as exc:
            raise AppError(
                code="APPROVAL_TRANSITION_INVALID",
                message="현재 상태에서는 요청한 승인 결정을 적용할 수 없습니다.",
                status_code=409,
            ) from exc
        if (
            data.decision is ApprovalDecisionKind.APPROVE
            and quorum
            and not final_stage
        ):
            request.current_stage_index += 1
            request.stage_due_at = _stage_due_at(
                now, stages[request.current_stage_index]
            )
        request.status = new_status.value
        if new_status is ApprovalRequestStatus.APPROVED:
            request.approved_by = principal.subject_id
            request.approved_at = now
            request.approved_content_version_id = request.content_version_id
            request.approved_content_hash = request.content_hash
            request.stage_due_at = None
        elif new_status in {
            ApprovalRequestStatus.REJECTED,
            ApprovalRequestStatus.CHANGES_REQUESTED,
        }:
            request.stage_due_at = None
        decision = ApprovalDecision(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            approval_request_id=request.id,
            content_version_id=request.content_version_id,
            content_hash=request.content_hash,
            stage_key=stage.key,
            stage_index=request.current_stage_index if not quorum else (
                request.current_stage_index - 1
                if data.decision is ApprovalDecisionKind.APPROVE and not final_stage
                else request.current_stage_index
            ),
            decision=data.decision.value,
            from_status=old_status.value,
            to_status=new_status.value,
            comment=data.comment,
            authentication_method=principal.authentication_method,
            decided_by=principal.subject_id,
            decided_at=now,
        )
        self.session.add(decision)
        self.session.add(
            _approval_state_event(
                principal,
                request,
                event_type="DECIDED",
                from_status=old_status.value,
                details={
                    "decision_id": str(decision.id),
                    "decision": decision.decision,
                    "stage_key": stage.key,
                    "quorum_reached": quorum,
                    "final_stage": final_stage,
                },
            )
        )
        await self.repo.flush("approval_decision")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.APPROVAL_DECIDED,
            action=(
                "quality.approval.approved"
                if new_status is ApprovalRequestStatus.APPROVED
                else "quality.approval.decision_recorded"
            ),
            target_type="approval_request",
            target_id=request.id,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            details={
                "decision_id": str(decision.id),
                "decision": decision.decision,
                "stage_key": stage.key,
                "status": request.status,
                "content_hash": request.content_hash,
            },
        )
        return ApprovalDecisionResult(request=request, decision=decision)

    async def invalidate_approvals_after_edit(
        self,
        principal: Principal,
        content_id: UUID,
        data: ApprovalInvalidationCreate,
    ) -> list[ApprovalRequest]:
        current = await self._exact_content(
            principal.workspace_id,
            content_id,
            data.new_content_version_id,
            data.new_content_hash,
            require_current=True,
        )
        return await self.invalidate_approvals_for_content_change(
            principal,
            content_id,
            new_content_version_id=current.content_version_id,
            new_content_hash=current.content_hash,
            reason=data.reason,
            force=False,
        )

    async def invalidate_approvals_for_content_change(
        self,
        principal: Principal,
        content_id: UUID,
        *,
        new_content_version_id: UUID,
        new_content_hash: str,
        reason: str,
        force: bool,
    ) -> list[ApprovalRequest]:
        """Invalidate active approvals from a trusted content mutation boundary."""

        await self._scope(principal.workspace_id)
        predicates = [
            ApprovalRequest.workspace_id == principal.workspace_id,
            ApprovalRequest.content_id == content_id,
            ApprovalRequest.status.in_(
                [
                    ApprovalRequestStatus.PENDING.value,
                    ApprovalRequestStatus.APPROVED.value,
                ]
            ),
        ]
        if not force:
            predicates.append(
                (ApprovalRequest.content_version_id != new_content_version_id)
                | (ApprovalRequest.content_hash != new_content_hash)
            )
        requests = list(
            await self.session.scalars(
                select(ApprovalRequest)
                .where(*predicates)
                .with_for_update()
            )
        )
        for request in requests:
            await self._invalidate_request(
                principal,
                request,
                reason=reason,
                new_version_id=new_content_version_id,
                new_content_hash=new_content_hash,
            )
        if requests:
            await self.repo.flush("approval_invalidation")
            await self._record_change(
                principal,
                activity_kind=ActivityKind.APPROVAL_INVALIDATED,
                action="quality.approval.content_edit_invalidated",
                target_type="content",
                target_id=content_id,
                content_id=content_id,
                content_version_id=new_content_version_id,
                details={
                    "new_content_hash": new_content_hash,
                    "invalidated_request_ids": [str(item.id) for item in requests],
                    "reason": reason,
                    "forced_by_context_change": force,
                },
            )
        return requests

    async def approval_proof(
        self, principal: Principal, request_id: UUID
    ) -> dict[str, Any]:
        request = await self.repo.approval_request(principal.workspace_id, request_id)
        if request.status != ApprovalRequestStatus.APPROVED.value:
            raise AppError(
                code="CONTENT_NOT_APPROVED",
                message="승인 완료 상태의 요청만 승인 증명으로 사용할 수 있습니다.",
                status_code=409,
            )
        current = await self.contents.current(principal.workspace_id, request.content_id)
        if not exact_content_version_matches(
            expected_version_id=request.content_version_id,
            expected_content_hash=request.content_hash,
            actual_version_id=current.content_version_id,
            actual_content_hash=current.content_hash,
        ):
            raise AppError(
                code="APPROVAL_INVALIDATED_BY_EDIT",
                message="승인 후 콘텐츠가 편집되어 승인 증명이 더 이상 유효하지 않습니다.",
                status_code=409,
                remediation={
                    "action": "REQUEST_NEW_QUALITY_ASSESSMENT_AND_APPROVAL",
                    "current_content_version_id": str(current.content_version_id),
                    "current_content_hash": current.content_hash,
                },
            )
        if (
            request.approved_by is None
            or request.approved_at is None
            or request.approved_content_version_id != request.content_version_id
            or request.approved_content_hash != request.content_hash
        ):
            raise AppError(
                code="APPROVAL_PROOF_INCOMPLETE",
                message="승인 증명에 필요한 버전 또는 승인자 정보가 없습니다.",
                status_code=409,
            )
        return {
            "approval_request_id": request.id,
            "content_id": request.content_id,
            "content_version_id": request.content_version_id,
            "content_hash": request.content_hash,
            "assessment_id": request.assessment_id,
            "assessment_hash": request.assessment_hash,
            "approved_by": request.approved_by,
            "approved_at": request.approved_at,
            "approval_stages_hash": request.approval_stages_hash,
            "quality_config_hash": request.quality_config_hash,
        }

    async def list_approval_decisions(
        self, principal: Principal, request_id: UUID
    ) -> list[ApprovalDecision]:
        await self.repo.approval_request(principal.workspace_id, request_id)
        return list(
            await self.session.scalars(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.workspace_id == principal.workspace_id,
                    ApprovalDecision.approval_request_id == request_id,
                )
                .order_by(ApprovalDecision.decided_at, ApprovalDecision.id)
            )
        )

    async def create_comment(
        self, principal: Principal, data: QualityCommentCreate
    ) -> QualityCommentRead:
        content_id, content_version_id = await self._comment_target(
            principal.workspace_id, data.target_type, data.target_id
        )
        await self.memberships.require_active(
            principal.workspace_id, set(data.mentioned_user_ids)
        )
        if data.parent_comment_id is not None:
            parent = await self.repo.comment(
                principal.workspace_id, data.parent_comment_id
            )
            if parent.target_type != data.target_type.value or parent.target_id != data.target_id:
                raise AppError(
                    code="COMMENT_PARENT_TARGET_MISMATCH",
                    message="답글은 같은 품질 또는 승인 대상에만 연결할 수 있습니다.",
                    status_code=422,
                )
        comment = QualityComment(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            target_type=data.target_type.value,
            target_id=data.target_id,
            content_id=content_id,
            content_version_id=content_version_id,
            parent_comment_id=data.parent_comment_id,
            body=data.body,
            author_id=principal.subject_id,
            lock_version=1,
        )
        self.session.add(comment)
        mentions = [
            QualityMention(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                comment_id=comment.id,
                mentioned_user_id=user_id,
                mentioned_by=principal.subject_id,
            )
            for user_id in data.mentioned_user_ids
        ]
        self.session.add_all(mentions)
        await self.repo.flush("quality_comment")
        await self._record_change(
            principal,
            activity_kind=ActivityKind.COMMENT_CREATED,
            action="quality.comment.created",
            target_type="quality_comment",
            target_id=comment.id,
            content_id=content_id,
            content_version_id=content_version_id,
            details={
                "comment_target_type": data.target_type.value,
                "comment_target_id": str(data.target_id),
                "mentioned_user_ids": [str(item) for item in data.mentioned_user_ids],
            },
        )
        return _comment_read(comment, data.mentioned_user_ids)

    async def list_comments(
        self,
        principal: Principal,
        *,
        target_type: CollaborationTarget,
        target_id: UUID,
    ) -> list[QualityCommentRead]:
        await self._comment_target(principal.workspace_id, target_type, target_id)
        comments = list(
            await self.session.scalars(
                select(QualityComment)
                .where(
                    QualityComment.workspace_id == principal.workspace_id,
                    QualityComment.target_type == target_type.value,
                    QualityComment.target_id == target_id,
                )
                .order_by(QualityComment.created_at, QualityComment.id)
            )
        )
        comment_ids = [item.id for item in comments]
        mentions = list(
            await self.session.scalars(
                select(QualityMention).where(
                    QualityMention.workspace_id == principal.workspace_id,
                    QualityMention.comment_id.in_(comment_ids),
                )
            )
        ) if comment_ids else []
        mentions_by_comment: dict[UUID, list[UUID]] = {}
        for mention in mentions:
            mentions_by_comment.setdefault(mention.comment_id, []).append(
                mention.mentioned_user_id
            )
        return [
            _comment_read(item, mentions_by_comment.get(item.id, []))
            for item in comments
        ]

    async def resolve_comment(
        self,
        principal: Principal,
        comment_id: UUID,
        data: QualityCommentResolve,
    ) -> QualityCommentRead:
        comment = await self.repo.comment(
            principal.workspace_id, comment_id, for_update=True
        )
        _assert_lock("quality_comment", data.expected_lock_version, comment.lock_version)
        if data.resolved:
            comment.resolved_at = datetime.now(UTC)
            comment.resolved_by = principal.subject_id
        else:
            comment.resolved_at = None
            comment.resolved_by = None
        await self.repo.flush("quality_comment")
        mentioned_ids = list(
            await self.session.scalars(
                select(QualityMention.mentioned_user_id).where(
                    QualityMention.workspace_id == principal.workspace_id,
                    QualityMention.comment_id == comment.id,
                )
            )
        )
        await self._record_change(
            principal,
            activity_kind=ActivityKind.COMMENT_RESOLVED,
            action="quality.comment.resolution_changed",
            target_type="quality_comment",
            target_id=comment.id,
            content_id=comment.content_id,
            content_version_id=comment.content_version_id,
            details={"resolved": data.resolved},
        )
        return _comment_read(comment, mentioned_ids)

    async def list_activity(
        self,
        principal: Principal,
        *,
        content_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[QualityActivity]:
        query = select(QualityActivity).where(
            QualityActivity.workspace_id == principal.workspace_id
        )
        if content_id is not None:
            query = query.where(QualityActivity.content_id == content_id)
        return list(
            await self.session.scalars(
                query.order_by(QualityActivity.created_at.desc(), QualityActivity.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self.session, workspace_id)

    async def _latest_config(self, workspace_id: UUID) -> WorkspaceQualityConfig:
        config = await self.session.scalar(
            select(WorkspaceQualityConfig)
            .where(WorkspaceQualityConfig.workspace_id == workspace_id)
            .order_by(WorkspaceQualityConfig.version.desc())
            .limit(1)
        )
        if config is None:
            raise AppError(
                code="QUALITY_CONFIG_REQUIRED",
                message="보고서를 만들기 전에 버전이 지정된 워크스페이스 품질 설정이 필요합니다.",
                status_code=409,
            )
        return config

    async def _rule_sets_for_report(
        self, workspace_id: UUID, requested_ids: list[UUID]
    ) -> list[QualityRuleSet]:
        now = datetime.now(UTC)
        if requested_ids:
            if len(requested_ids) != len(set(requested_ids)):
                raise AppError(
                    code="QUALITY_RULE_SET_DUPLICATE",
                    message="규칙 세트 식별자가 중복되었습니다.",
                    status_code=422,
                )
            values = list(
                await self.session.scalars(
                    select(QualityRuleSet).where(
                        QualityRuleSet.workspace_id == workspace_id,
                        QualityRuleSet.id.in_(requested_ids),
                        QualityRuleSet.effective_at <= now,
                    )
                )
            )
            if len(values) != len(requested_ids):
                raise AppError(
                    code="QUALITY_RULE_SET_INVALID",
                    message="같은 워크스페이스에서 효력이 발생한 규칙 세트만 사용할 수 있습니다.",
                    status_code=422,
                )
            return sorted(values, key=lambda item: (POLICY_LAYER_PRIORITY[PolicyLayer(item.layer)], item.name))
        values = list(
            await self.session.scalars(
                select(QualityRuleSet)
                .where(
                    QualityRuleSet.workspace_id == workspace_id,
                    QualityRuleSet.effective_at <= now,
                )
                .order_by(
                    QualityRuleSet.layer,
                    QualityRuleSet.name,
                    QualityRuleSet.version.desc(),
                )
            )
        )
        latest_by_identity: dict[tuple[str, str], QualityRuleSet] = {}
        for item in values:
            latest_by_identity.setdefault((item.layer, item.name), item)
        return sorted(
            latest_by_identity.values(),
            key=lambda item: (POLICY_LAYER_PRIORITY[PolicyLayer(item.layer)], item.name),
        )

    def _validate_analyzer_requirements(
        self,
        kind: ReportKind,
        data: BaseReportCreate,
        rule_sets: list[QualityRuleSet],
    ) -> None:
        for rule_set in rule_sets:
            requirements = rule_set.analyzer_requirements_json.get(kind.value, {})
            if not isinstance(requirements, dict):
                continue
            expected_name = requirements.get("analyzer_name")
            expected_version = requirements.get("analyzer_version")
            expected_model = requirements.get("model_version")
            expected_dictionary = requirements.get("dictionary_version")
            mismatches: list[str] = []
            if expected_name and expected_name != data.analyzer.analyzer_name:
                mismatches.append("analyzer_name")
            if expected_version and expected_version != data.analyzer.analyzer_version:
                mismatches.append("analyzer_version")
            if expected_model and expected_model != data.analyzer.model_version:
                mismatches.append("model_version")
            if expected_dictionary and expected_dictionary != data.analyzer.dictionary_version:
                mismatches.append("dictionary_version")
            if mismatches:
                raise AppError(
                    code="ANALYZER_VERSION_NOT_ALLOWED",
                    message="선택한 규칙 세트가 요구하는 분석기 버전과 일치하지 않습니다.",
                    status_code=422,
                    fields=[
                        {"path": f"analyzer.{field}", "reason": rule_set.snapshot_hash}
                        for field in mismatches
                    ],
                )

    async def _exact_content(
        self,
        workspace_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        content_hash: str,
        *,
        require_current: bool,
    ) -> ContentVersionSnapshot:
        content = await self.contents.resolve(
            workspace_id, content_id, content_version_id
        )
        if content.content_hash != content_hash:
            raise AppError(
                code="CONTENT_HASH_MISMATCH",
                message="콘텐츠 버전의 저장된 해시와 요청 해시가 일치하지 않습니다.",
                status_code=409,
                fields=[
                    {"path": "expected_content_hash", "reason": content_hash},
                    {"path": "actual_content_hash", "reason": content.content_hash},
                ],
            )
        if require_current and not content.is_current:
            raise AppError(
                code="CONTENT_VERSION_NOT_CURRENT",
                message="현재 콘텐츠 버전만 승인 요청에 사용할 수 있습니다.",
                status_code=409,
                remediation={
                    "current_content_version_id": (
                        str(content.current_version_id)
                        if content.current_version_id
                        else None
                    )
                },
            )
        return content

    async def _assert_assessment_approval_eligible(
        self, workspace_id: UUID, assessment: QualityAssessment
    ) -> None:
        blocking_ids = [UUID(item) for item in assessment.blocking_policy_event_ids]
        if not blocking_ids:
            if assessment.decision != AssessmentDecision.PASS.value:
                raise AppError(
                    code="QUALITY_ASSESSMENT_NOT_PASSED",
                    message="통과한 품질 평가만 승인 요청에 사용할 수 있습니다.",
                    status_code=409,
                )
            return
        overrides = set(
            await self.session.scalars(
                select(PolicyOverride.policy_event_id).where(
                    PolicyOverride.workspace_id == workspace_id,
                    PolicyOverride.policy_event_id.in_(blocking_ids),
                )
            )
        )
        non_overrideable = {
            UUID(item) for item in assessment.non_overrideable_policy_event_ids
        }
        if non_overrideable:
            raise AppError(
                code="QUALITY_HARD_BLOCK",
                message="예외 승인할 수 없는 상위 정책 차단이 있어 승인 요청을 만들 수 없습니다.",
                status_code=409,
                fields=[
                    {"path": "policy_event_id", "reason": str(item)}
                    for item in sorted(non_overrideable, key=str)
                ],
            )
        unresolved = set(blocking_ids).difference(overrides)
        if unresolved:
            raise AppError(
                code="QUALITY_POLICY_OVERRIDE_REQUIRED",
                message="승인 요청 전에 예외 가능한 정책 차단을 모두 해소해야 합니다.",
                status_code=409,
                fields=[
                    {"path": "policy_event_id", "reason": str(item)}
                    for item in sorted(unresolved, key=str)
                ],
            )

    async def _invalidate_request(
        self,
        principal: Principal,
        request: ApprovalRequest,
        *,
        reason: str,
        new_version_id: UUID,
        new_content_hash: str,
    ) -> None:
        old_status = request.status
        request.status = ApprovalRequestStatus.INVALIDATED.value
        request.invalidated_at = datetime.now(UTC)
        request.invalidated_by = principal.subject_id
        request.invalidation_reason = reason
        request.stage_due_at = None
        self.session.add(
            _approval_state_event(
                principal,
                request,
                event_type="INVALIDATED_BY_EDIT",
                from_status=old_status,
                details={
                    "new_content_version_id": str(new_version_id),
                    "new_content_hash": new_content_hash,
                    "reason": reason,
                },
            )
        )
        await self._record_change(
            principal,
            activity_kind=ActivityKind.APPROVAL_INVALIDATED,
            action="quality.approval.invalidated",
            target_type="approval_request",
            target_id=request.id,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            details={
                "approved_or_requested_content_hash": request.content_hash,
                "new_content_version_id": str(new_version_id),
                "new_content_hash": new_content_hash,
                "reason": reason,
            },
        )

    async def _comment_target(
        self,
        workspace_id: UUID,
        target_type: CollaborationTarget,
        target_id: UUID,
    ) -> tuple[UUID, UUID]:
        if target_type is CollaborationTarget.REPORT:
            target = await self.repo.report(workspace_id, target_id)
        elif target_type is CollaborationTarget.ASSESSMENT:
            target = await self.repo.assessment(workspace_id, target_id)
        elif target_type is CollaborationTarget.APPROVAL_REQUEST:
            target = await self.repo.approval_request(workspace_id, target_id)
        else:
            target = await self.repo.policy_event(workspace_id, target_id)
        return target.content_id, target.content_version_id

    async def _record_change(
        self,
        principal: Principal,
        *,
        activity_kind: ActivityKind,
        action: str,
        target_type: str,
        target_id: UUID,
        content_id: UUID | None,
        content_version_id: UUID | None,
        details: dict[str, Any],
    ) -> None:
        activity = QualityActivity(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content_id,
            content_version_id=content_version_id,
            activity_kind=activity_kind.value,
            target_type=target_type,
            target_id=target_id,
            details_json=details,
            actor_id=principal.subject_id,
        )
        self.session.add(activity)
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        await add_outbox_event(
            self.session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "content_id": str(content_id) if content_id else None,
                "content_version_id": (
                    str(content_version_id) if content_version_id else None
                ),
                **details,
            },
        )


def _normalized_rule(layer: PolicyLayer, rule: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(rule)
    if (
        layer in {PolicyLayer.LAW, PolicyLayer.COPYRIGHT}
        and normalized.get("severity") == FindingSeverity.BLOCK.value
    ):
        normalized["hard_block"] = True
    is_blocking = (
        normalized.get("hard_block")
        or normalized.get("severity") == FindingSeverity.BLOCK.value
    )
    if is_blocking and layer in NON_OVERRIDEABLE_HARD_BLOCK_LAYERS:
        normalized["override_allowed"] = False
    normalized["layer"] = layer.value
    return normalized


def _rule_set_snapshot(rule_set: QualityRuleSet) -> dict[str, Any]:
    return {
        "id": str(rule_set.id),
        "layer": rule_set.layer,
        "name": rule_set.name,
        "version": rule_set.version,
        "rules": rule_set.rules_json,
        "analyzer_requirements": rule_set.analyzer_requirements_json,
        "snapshot_hash": rule_set.snapshot_hash,
        "effective_at": rule_set.effective_at.isoformat(),
    }


def _quality_config_snapshot(config: WorkspaceQualityConfig) -> dict[str, Any]:
    return {
        "id": str(config.id),
        "version": config.version,
        "minimum_total_score": str(config.minimum_total_score),
        "minimum_component_scores": config.minimum_component_scores,
        "required_report_kinds": config.required_report_kinds,
        "approval_stages": config.approval_stages,
        "threshold_override_allowed": config.threshold_override_allowed,
        "config_hash": config.config_hash,
    }


def _policy_inputs(data: ReportCreate) -> list[PolicyFindingInput]:
    values = list(data.hard_blockers)
    if isinstance(data, SafetyPolicyReportCreate):
        values.extend(data.detail.policy_findings)
    by_key: dict[str, PolicyFindingInput] = {}
    for item in values:
        if item.event_key in by_key:
            raise AppError(
                code="POLICY_EVENT_KEY_DUPLICATE",
                message="한 보고서 안에서 정책 이벤트 키는 고유해야 합니다.",
                status_code=422,
                fields=[{"path": "event_key", "reason": item.event_key}],
            )
        by_key[item.event_key] = item
    return list(by_key.values())


def _normalize_policy_input(item: PolicyFindingInput) -> PolicyFindingInput:
    hard_block = item.hard_block
    override_allowed = item.override_allowed
    action = item.action
    severity = item.severity
    if action is PolicyAction.BLOCK:
        severity = FindingSeverity.BLOCK
    if item.layer in {PolicyLayer.LAW, PolicyLayer.COPYRIGHT} and (
        severity is FindingSeverity.BLOCK
    ):
        hard_block = True
    if hard_block or severity is FindingSeverity.BLOCK:
        action = PolicyAction.BLOCK
    if (
        (hard_block or severity is FindingSeverity.BLOCK)
        and item.layer in NON_OVERRIDEABLE_HARD_BLOCK_LAYERS
    ):
        override_allowed = False
    return item.model_copy(
        update={
            "hard_block": hard_block,
            "override_allowed": override_allowed,
            "action": action,
            "severity": severity,
        }
    )


def _new_report_detail(
    workspace_id: UUID,
    report_id: UUID,
    kind: ReportKind,
    data: ReportCreate,
) -> object:
    common = {"id": uuid4(), "workspace_id": workspace_id, "report_id": report_id}
    if kind is ReportKind.MORPHOLOGY and isinstance(data, MorphologyReportCreate):
        return MorphologyReport(
            **common,
            token_count=data.detail.token_count,
            sentence_count=data.detail.sentence_count,
            unknown_token_rate=data.detail.unknown_token_rate,
            spacing_issues=data.detail.spacing_issues,
            grammar_issues=data.detail.grammar_issues,
            token_analysis=data.detail.token_analysis,
            metrics_json=data.detail.metrics,
        )
    if kind is ReportKind.NATURALNESS and isinstance(data, NaturalnessReportCreate):
        return NaturalnessReport(
            **common,
            naturalness_score=data.detail.naturalness_score,
            usefulness_score=data.detail.usefulness_score,
            readability_score=data.detail.readability_score,
            brand_fit_score=data.detail.brand_fit_score,
            fluency_metrics=data.detail.fluency_metrics,
            sentence_metrics=data.detail.sentence_metrics,
            awkward_expressions=data.detail.awkward_expressions,
        )
    if kind is ReportKind.SEO and isinstance(data, SEOReportCreate):
        return SEOReport(
            **common,
            search_intent_score=data.detail.search_intent_score,
            primary_keyword=data.detail.primary_keyword,
            keyword_metrics=data.detail.keyword_metrics,
            title_checks=data.detail.title_checks,
            heading_checks=data.detail.heading_checks,
            meta_checks=data.detail.meta_checks,
            recommendations=data.detail.recommendations,
        )
    if kind is ReportKind.DUPLICATION and isinstance(data, DuplicationReportCreate):
        return DuplicationReport(
            **common,
            originality_score=data.detail.originality_score,
            duplicate_ratio=data.detail.duplicate_ratio,
            algorithm=data.detail.algorithm,
            algorithm_version=data.detail.algorithm_version,
            corpus_snapshot_hash=data.detail.corpus_snapshot_hash,
            near_duplicates=data.detail.near_duplicates,
            cannibalization_findings=data.detail.cannibalization_findings,
        )
    if kind is ReportKind.FACT_CITATION and isinstance(data, FactCitationReportCreate):
        citation_link_rate = (
            (data.detail.linked_citation_count / data.detail.citation_count) * 100
            if data.detail.citation_count
            else 0.0
        )
        return FactCitationReport(
            **common,
            accuracy_score=data.detail.accuracy_score,
            claim_count=data.detail.claim_count,
            supported_claim_count=data.detail.supported_claim_count,
            citation_count=data.detail.citation_count,
            linked_citation_count=data.detail.linked_citation_count,
            citation_link_rate=round(citation_link_rate, 2),
            claim_citation_graph=data.detail.claim_citation_graph,
            unsupported_claims=data.detail.unsupported_claims,
            invalid_citations=data.detail.invalid_citations,
        )
    if kind is ReportKind.SAFETY_POLICY and isinstance(data, SafetyPolicyReportCreate):
        return SafetyPolicyReport(
            **common,
            compliance_score=data.detail.compliance_score,
            policy_findings=[
                _normalize_policy_input(item).model_dump(mode="json")
                for item in data.detail.policy_findings
            ],
            safety_categories=data.detail.safety_categories,
            required_disclosures=data.detail.required_disclosures,
            banned_claim_matches=data.detail.banned_claim_matches,
        )
    raise AppError(
        code="QUALITY_REPORT_PAYLOAD_KIND_MISMATCH",
        message="보고서 경로와 상세 결과 형식이 일치하지 않습니다.",
        status_code=422,
    )


def _new_policy_event(
    principal: Principal,
    *,
    content: ContentVersionSnapshot,
    report: QualityReport | None,
    assessment_id: UUID | None,
    finding: PolicyFindingInput,
    rule_snapshot_hash: str,
    policy_snapshot_hash: str,
    event_id: UUID | None = None,
) -> PolicyEvent:
    payload = {
        "content_version_id": str(content.content_version_id),
        "content_hash": content.content_hash,
        "report_id": str(report.id) if report else None,
        "assessment_id": str(assessment_id) if assessment_id else None,
        "event_key": finding.event_key,
        "layer": finding.layer.value,
        "rule_code": finding.rule_code,
        "action": finding.action.value,
        "severity": finding.severity.value,
        "hard_block": finding.hard_block,
        "override_allowed": finding.override_allowed,
        "message": finding.message,
        "evidence": finding.evidence,
        "rule_snapshot_hash": rule_snapshot_hash,
        "policy_snapshot_hash": policy_snapshot_hash,
    }
    return PolicyEvent(
        id=event_id or uuid4(),
        workspace_id=principal.workspace_id,
        content_id=content.content_id,
        content_version_id=content.content_version_id,
        content_hash=content.content_hash,
        report_id=report.id if report else None,
        assessment_id=assessment_id,
        event_key=finding.event_key,
        layer=finding.layer.value,
        rule_code=finding.rule_code,
        action=finding.action.value,
        severity=finding.severity.value,
        hard_block=finding.hard_block,
        override_allowed=finding.override_allowed,
        priority=POLICY_LAYER_PRIORITY[finding.layer],
        message=finding.message,
        evidence_json=finding.evidence,
        rule_snapshot_hash=rule_snapshot_hash,
        policy_snapshot_hash=policy_snapshot_hash,
        event_hash=canonical_json_hash(payload),
        created_by=principal.subject_id,
    )


def _report_read(report: QualityReport, detail: object) -> QualityReportRead:
    return QualityReportRead.model_validate(
        {
            "id": report.id,
            "workspace_id": report.workspace_id,
            "content_id": report.content_id,
            "content_version_id": report.content_version_id,
            "content_hash": report.content_hash,
            "report_kind": report.report_kind,
            "analyzer_name": report.analyzer_name,
            "analyzer_version": report.analyzer_version,
            "model_name": report.model_name,
            "model_version": report.model_version,
            "dictionary_name": report.dictionary_name,
            "dictionary_version": report.dictionary_version,
            "input_hash": report.input_hash,
            "analyzer_config_snapshot": report.analyzer_config_snapshot,
            "analyzer_config_hash": report.analyzer_config_hash,
            "rule_snapshot": report.rule_snapshot,
            "rule_snapshot_hash": report.rule_snapshot_hash,
            "policy_snapshot": report.policy_snapshot,
            "policy_snapshot_hash": report.policy_snapshot_hash,
            "summary_json": report.summary_json,
            "findings_json": report.findings_json,
            "hard_blockers_json": report.hard_blockers_json,
            "report_hash": report.report_hash,
            "created_by": report.created_by,
            "created_at": report.created_at,
            "detail": _detail_payload(detail),
        }
    )


def _detail_payload(detail: object) -> dict[str, Any]:
    table = getattr(detail, "__table__")
    return {
        column.name: getattr(detail, column.name)
        for column in table.columns
        if column.name not in {"id", "workspace_id", "report_id"}
    }


def _component_scores(details: dict[ReportKind, object]) -> dict[str, Decimal]:
    fact = details[ReportKind.FACT_CITATION]
    naturalness = details[ReportKind.NATURALNESS]
    duplication = details[ReportKind.DUPLICATION]
    seo = details[ReportKind.SEO]
    safety = details[ReportKind.SAFETY_POLICY]
    if not isinstance(fact, FactCitationReport):
        raise _detail_type_error(ReportKind.FACT_CITATION)
    if not isinstance(naturalness, NaturalnessReport):
        raise _detail_type_error(ReportKind.NATURALNESS)
    if not isinstance(duplication, DuplicationReport):
        raise _detail_type_error(ReportKind.DUPLICATION)
    if not isinstance(seo, SEOReport):
        raise _detail_type_error(ReportKind.SEO)
    if not isinstance(safety, SafetyPolicyReport):
        raise _detail_type_error(ReportKind.SAFETY_POLICY)
    return {
        "accuracy": Decimal(str(fact.accuracy_score)),
        "usefulness": Decimal(str(naturalness.usefulness_score)),
        "originality": Decimal(str(duplication.originality_score)),
        "search_intent": Decimal(str(seo.search_intent_score)),
        "readability": Decimal(str(naturalness.readability_score)),
        "brand_fit": Decimal(str(naturalness.brand_fit_score)),
        "compliance": Decimal(str(safety.compliance_score)),
    }


def _detail_type_error(kind: ReportKind) -> AppError:
    return AppError(
        code="QUALITY_REPORT_DETAIL_MISMATCH",
        message="품질 산식에 사용할 보고서 상세 형식이 올바르지 않습니다.",
        status_code=409,
        fields=[{"path": "report_kind", "reason": kind.value}],
    )


def _policy_finding_from_event(event: PolicyEvent) -> PolicyFinding:
    return PolicyFinding(
        event_key=str(event.id),
        layer=PolicyLayer(event.layer),
        rule_code=event.rule_code,
        severity=FindingSeverity(event.severity),
        hard_block=event.hard_block,
        override_allowed=event.override_allowed,
    )


def _assert_report_content(
    report: QualityReport, content: ContentVersionSnapshot
) -> None:
    if (
        report.content_id != content.content_id
        or report.content_version_id != content.content_version_id
        or report.content_hash != content.content_hash
    ):
        raise AppError(
            code="QUALITY_REPORT_CONTENT_MISMATCH",
            message="모든 품질 보고서는 같은 콘텐츠 버전과 해시에 고정되어야 합니다.",
            status_code=422,
        )


def _policy_event_snapshot_hash(event: PolicyEvent) -> str:
    return event.event_hash


def _override_forbidden(event: PolicyEvent) -> AppError:
    return AppError(
        code="POLICY_OVERRIDE_FORBIDDEN",
        message="법률·저작권·플랫폼·서비스 hard block 또는 예외 불가 정책은 무시할 수 없습니다.",
        status_code=409,
        fields=[
            {"path": "policy_event_id", "reason": str(event.id)},
            {"path": "layer", "reason": event.layer},
            {"path": "rule_code", "reason": event.rule_code},
        ],
    )


def _stage_due_at(now: datetime, stage: ApprovalStageConfig) -> datetime | None:
    return now + timedelta(seconds=stage.due_seconds) if stage.due_seconds else None


def _approval_state_event(
    principal: Principal,
    request: ApprovalRequest,
    *,
    event_type: str,
    from_status: str | None,
    details: dict[str, Any],
) -> ApprovalStateEvent:
    return ApprovalStateEvent(
        id=uuid4(),
        workspace_id=principal.workspace_id,
        approval_request_id=request.id,
        event_type=event_type,
        from_status=from_status,
        to_status=request.status,
        content_version_id=request.content_version_id,
        content_hash=request.content_hash,
        details_json=details,
        actor_id=principal.subject_id,
    )


def _comment_read(
    comment: QualityComment, mentioned_user_ids: list[UUID]
) -> QualityCommentRead:
    return QualityCommentRead.model_validate(
        {
            "id": comment.id,
            "workspace_id": comment.workspace_id,
            "target_type": comment.target_type,
            "target_id": comment.target_id,
            "content_id": comment.content_id,
            "content_version_id": comment.content_version_id,
            "parent_comment_id": comment.parent_comment_id,
            "body": comment.body,
            "author_id": comment.author_id,
            "resolved_at": comment.resolved_at,
            "resolved_by": comment.resolved_by,
            "lock_version": comment.lock_version,
            "created_at": comment.created_at,
            "mentioned_user_ids": mentioned_user_ids,
        }
    )


def _assert_expected_version(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise AppError(
            code="CONFIG_VERSION_CONFLICT",
            message="설정 버전이 변경되었습니다. 최신 버전을 다시 조회해 주세요.",
            status_code=409,
            fields=[
                {"path": "resource", "reason": resource},
                {"path": "expected_previous_version", "reason": str(expected)},
                {"path": "actual_previous_version", "reason": str(actual)},
            ],
        )


def _assert_lock(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise AppError(
            code="OPTIMISTIC_LOCK_CONFLICT",
            message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
            status_code=409,
            fields=[
                {"path": "resource", "reason": resource},
                {"path": "expected_lock_version", "reason": str(expected)},
                {"path": "actual_lock_version", "reason": str(actual)},
            ],
        )


def _assert_hash(resource: str, expected: str, actual: str) -> None:
    if expected != actual:
        raise AppError(
            code="SNAPSHOT_HASH_CONFLICT",
            message="고정된 스냅샷 해시가 변경되었습니다.",
            status_code=409,
            fields=[
                {"path": "resource", "reason": resource},
                {"path": "expected_hash", "reason": expected},
                {"path": "actual_hash", "reason": actual},
            ],
        )
