"""Bulk job orchestration with row idempotency and fail-closed budget boundaries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.bulk.enums import (
    BulkAttemptOutcome,
    BulkCommandKind,
    BulkInputKind,
    BulkPriority,
    BulkRowState,
    RETRYABLE_ROW_STATES,
)
from blogops.domain.bulk.ingestion import VerifiedBulkSnapshot, iter_normalized_csv_rows
from blogops.domain.bulk.models import (
    BulkInputFile,
    BulkJob,
    BulkJobCommand,
    BulkMapping,
    BulkRow,
    BulkRowAttempt,
    BulkSchedule,
)
from blogops.domain.bulk.providers import BulkBudgetGate
from blogops.domain.bulk.rules import (
    canonical_hash,
    evaluate_budget_boundary,
    evaluate_spam_gate,
    validate_mapping,
    validate_row_capacity,
)
from blogops.domain.bulk.schemas import (
    BulkCommandRequest,
    BulkJobCreate,
    BulkMappingCreate,
    BulkRowsCommand,
    BulkScheduleCreate,
)
from blogops.domain.generation.enums import VersionStatus
from blogops.domain.generation.models import TemplateVersion
from blogops.domain.jobs.state import JobState, TERMINAL_JOB_STATES, ensure_job_transition
from blogops.domain.planning.enums import CampaignStatus
from blogops.domain.planning.models import Campaign
from blogops.domain.quality.enums import ApprovalRequestStatus, AssessmentDecision
from blogops.domain.quality.models import ApprovalRequest, QualityAssessment
from blogops.domain.media.rules import validate_private_object_ref
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_OUTBOX_SCHEMA_VERSION = "1.0"
_COST_QUANTUM = Decimal("0.000001")
_TRUSTED_INGESTION_VERSION = "bulk-byte-verifier-v1"
_PROCESSED_ROW_STATES = frozenset(
    {
        BulkRowState.QUALITY_BLOCKED.value,
        BulkRowState.INVALID.value,
        BulkRowState.DUPLICATE.value,
        BulkRowState.WAITING_REVIEW.value,
        BulkRowState.APPROVED.value,
        BulkRowState.REJECTED.value,
        BulkRowState.SUCCEEDED.value,
        BulkRowState.RETRYABLE_FAILED.value,
        BulkRowState.FINAL_FAILED.value,
        BulkRowState.CANCELLED.value,
    }
)


class BulkService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _job(
        self, workspace_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> BulkJob:
        query = select(BulkJob).where(
            BulkJob.workspace_id == workspace_id,
            BulkJob.id == job_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise _not_found("BULK_JOB", "대량 작업")
        return value

    async def _record(
        self,
        *,
        principal: Principal,
        action: str,
        aggregate_type: str,
        aggregate_id: UUID,
        details: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self._session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=aggregate_type,
            target_id=str(aggregate_id),
            details=details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=action,
            schema_version=_OUTBOX_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(aggregate_id),
                **details,
            },
        )

    async def register_input(
        self,
        principal: Principal,
        snapshot: VerifiedBulkSnapshot,
    ) -> BulkInputFile:
        """Persist only evidence produced by byte-level snapshot verification."""

        await self._scope(principal.workspace_id)
        if not isinstance(snapshot, VerifiedBulkSnapshot):
            raise AppError(
                "BULK_VERIFIED_SNAPSHOT_REQUIRED",
                "서버 검증을 거친 입력 Snapshot만 등록할 수 있습니다.",
                409,
            )
        expected_prefix = (
            f"workspaces/{principal.workspace_id}/bulk/{snapshot.upload_id}/versions/"
            f"{snapshot.content_hash}."
        )
        validate_private_object_ref(
            snapshot.object_ref,
            workspace_id=str(principal.workspace_id),
            namespace="bulk",
        )
        object_extension = snapshot.object_ref.removeprefix(expected_prefix)
        if (
            not snapshot.object_ref.startswith(expected_prefix)
            or not object_extension
            or len(object_extension) > 16
            or not object_extension.isascii()
            or not object_extension.isalnum()
            or object_extension != object_extension.casefold()
        ):
            raise AppError(
                "BULK_SNAPSHOT_OBJECT_INVALID",
                "검증된 입력 Snapshot 저장 경로가 증거와 일치하지 않습니다.",
                409,
            )
        if (
            snapshot.input_kind not in {BulkInputKind.CSV, BulkInputKind.XLSX}
            or snapshot.malware_scan_status != "CLEAN"
            or not snapshot.malware_scanner.strip()
            or not snapshot.malware_scanner_version.strip()
            or not _is_sha256(snapshot.content_hash)
            or not _is_sha256(snapshot.malware_scan_result_hash)
            or snapshot.size_bytes < 1
            or snapshot.row_count < 1
            or not snapshot.headers
            or any(not value.strip() for value in snapshot.headers)
            or len(set(snapshot.headers)) != len(snapshot.headers)
        ):
            raise AppError(
                "BULK_SNAPSHOT_EVIDENCE_INVALID",
                "검증된 입력 Snapshot 증거가 완전하지 않습니다.",
                409,
            )
        existing = await self._session.scalar(
            select(BulkInputFile).where(
                BulkInputFile.workspace_id == principal.workspace_id,
                BulkInputFile.content_hash == snapshot.content_hash,
            )
        )
        if existing is not None:
            if (
                existing.malware_scan_status != "CLEAN"
                or not existing.malware_scan_result_hash
                or existing.metadata_json.get("trusted_ingestion")
                != _TRUSTED_INGESTION_VERSION
            ):
                raise AppError(
                    "BULK_EXISTING_INPUT_UNVERIFIED",
                    "동일한 Hash의 기존 입력이 신뢰된 수집 경로로 등록되지 않았습니다.",
                    409,
                )
            return existing
        value = BulkInputFile(
            workspace_id=principal.workspace_id,
            input_kind=snapshot.input_kind.value,
            name=snapshot.name,
            object_ref=snapshot.object_ref,
            content_hash=snapshot.content_hash,
            size_bytes=snapshot.size_bytes,
            row_count=snapshot.row_count,
            encoding=snapshot.encoding,
            delimiter=snapshot.delimiter,
            sheet_name=snapshot.sheet_name,
            sheet_range=snapshot.sheet_range,
            header_row=snapshot.header_row,
            headers=list(snapshot.headers),
            source_locator=None,
            source_locator_hash=None,
            source_connection_ref=None,
            source_secret_ref=None,
            malware_scan_status=snapshot.malware_scan_status,
            malware_scanner=snapshot.malware_scanner,
            malware_scanner_version=snapshot.malware_scanner_version,
            malware_scan_result_hash=snapshot.malware_scan_result_hash,
            metadata_json={
                **dict(snapshot.metadata),
                "trusted_ingestion": _TRUSTED_INGESTION_VERSION,
            },
            uploaded_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="bulk.input.registered",
            aggregate_type="bulk_input_file",
            aggregate_id=value.id,
            details={"kind": value.input_kind, "row_count": value.row_count},
        )
        return value

    async def create_mapping(
        self, principal: Principal, data: BulkMappingCreate
    ) -> BulkMapping:
        await self._scope(principal.workspace_id)
        available_columns = tuple(
            str(value.get("name")) if isinstance(value, dict) else str(value)
            for value in data.input_schema.get("columns", [])
        )
        problems = validate_mapping(
            available_columns=available_columns,
            column_mapping=data.column_mapping,
            required_variables=data.required_variables,
        )
        if problems:
            raise AppError(
                "BULK_MAPPING_INVALID",
                "입력 Schema와 변수 매핑이 일치하지 않습니다.",
                422,
                fields=[{"path": "column_mapping", "reason": value} for value in problems],
            )
        payload = data.model_dump(mode="json")
        digest = canonical_hash(payload)
        existing = await self._session.scalar(
            select(BulkMapping).where(
                BulkMapping.workspace_id == principal.workspace_id,
                BulkMapping.mapping_hash == digest,
            )
        )
        if existing is not None:
            return existing
        mapping = BulkMapping(
            workspace_id=principal.workspace_id,
            name=data.name,
            input_schema=data.input_schema,
            column_mapping=data.column_mapping,
            variable_schema=data.variable_schema,
            required_variables=data.required_variables,
            normalization_rules=data.normalization_rules,
            duplicate_policy={
                "exact_action": data.duplicate_action.value,
                "semantic_enabled": data.semantic_duplicate_enabled,
            },
            mapping_hash=digest,
            created_by=principal.subject_id,
        )
        self._session.add(mapping)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="bulk.mapping.created",
            aggregate_type="bulk_mapping",
            aggregate_id=mapping.id,
            details={"name": mapping.name, "mapping_hash": mapping.mapping_hash},
        )
        return mapping

    async def create_job(
        self,
        principal: Principal,
        data: BulkJobCreate,
        *,
        budget_gate: BulkBudgetGate,
    ) -> tuple[BulkJob, bool]:
        await self._scope(principal.workspace_id)
        request_payload = data.model_dump(mode="json")
        request_hash = canonical_hash(request_payload)
        existing = await self._session.scalar(
            select(BulkJob).where(
                BulkJob.workspace_id == principal.workspace_id,
                BulkJob.requested_by == principal.subject_id,
                BulkJob.operation == data.operation.value,
                BulkJob.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 멱등키가 다른 대량 작업 요청에 사용되었습니다.",
                    409,
                )
            return existing, False
        if data.priority == BulkPriority.URGENT and "bulk:manage" not in principal.permissions:
            raise AppError(
                "BULK_URGENT_PERMISSION_REQUIRED",
                "긴급 우선순위는 대량 작업 관리자만 선택할 수 있습니다.",
                403,
            )
        input_file = await self._session.scalar(
            select(BulkInputFile).where(
                BulkInputFile.workspace_id == principal.workspace_id,
                BulkInputFile.id == data.input_file_id,
            )
        )
        mapping = await self._session.scalar(
            select(BulkMapping).where(
                BulkMapping.workspace_id == principal.workspace_id,
                BulkMapping.id == data.mapping_id,
            )
        )
        template = await self._session.scalar(
            select(TemplateVersion).where(
                TemplateVersion.workspace_id == principal.workspace_id,
                TemplateVersion.id == data.template_version_id,
            )
        )
        campaign = await self._session.scalar(
            select(Campaign).where(
                Campaign.workspace_id == principal.workspace_id,
                Campaign.id == data.campaign_id,
            )
        )
        if input_file is None:
            raise _not_found("BULK_INPUT", "대량 입력 Snapshot")
        if mapping is None:
            raise _not_found("BULK_MAPPING", "대량 변수 Mapping")
        if template is None:
            raise _not_found("TEMPLATE_VERSION", "템플릿 버전")
        if campaign is None:
            raise _not_found("CAMPAIGN", "캠페인")
        if campaign.status != CampaignStatus.ACTIVE.value:
            raise AppError(
                "BULK_CAMPAIGN_NOT_ACTIVE",
                "활성 캠페인의 서버 정책만 대량 작업에 사용할 수 있습니다.",
                409,
            )
        if template.status != VersionStatus.PUBLISHED.value:
            raise AppError(
                "BULK_TEMPLATE_NOT_PUBLISHED",
                "발행된 템플릿 버전만 대량 작업에 사용할 수 있습니다.",
                409,
            )
        mapping_problems = validate_mapping(
            available_columns=tuple(input_file.headers),
            column_mapping=mapping.column_mapping,
            required_variables=mapping.required_variables,
        )
        if mapping_problems:
            raise AppError(
                "BULK_INPUT_MAPPING_MISMATCH",
                "고정된 입력 Snapshot과 변수 Mapping이 일치하지 않습니다.",
                409,
                fields=[
                    {"path": "mapping_id", "reason": value}
                    for value in mapping_problems
                ],
            )
        if input_file.malware_scan_status not in {"CLEAN", "NOT_REQUIRED"}:
            raise AppError(
                "BULK_INPUT_SCAN_REQUIRED",
                "악성코드 검사를 통과한 입력 Snapshot만 실행할 수 있습니다.",
                409,
            )
        if data.sample_size is not None and data.sample_size > input_file.row_count:
            raise AppError("BULK_SAMPLE_TOO_LARGE", "샘플 수가 입력 행 수보다 큽니다.", 422)
        campaign_quality_policy = campaign.generation_policy_snapshot.get("quality")
        _spam_policy_thresholds(campaign_quality_policy)
        reservation = await budget_gate.reserve(
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            job_key=data.idempotency_key,
            estimated_cost=data.estimated_cost,
            maximum_cost=data.maximum_cost,
            currency=data.currency.upper(),
        )
        if reservation.authorized_amount < data.estimated_cost:
            raise AppError("BULK_BUDGET_EXCEEDED", "대량 작업 비용 Hold가 부족합니다.", 402)
        entitlements = dict(reservation.entitlement_snapshot)
        entitled_limit_raw = entitlements.get("max_rows_per_bulk_job")
        entitled_limit = int(entitled_limit_raw) if entitled_limit_raw is not None else None
        validate_row_capacity(input_file.row_count, entitled_limit)
        concurrency_max = _required_positive_entitlement(
            entitlements, "max_bulk_concurrency"
        )
        daily_max = _required_positive_entitlement(
            entitlements, "max_bulk_daily_rows"
        )
        mapping_snapshot = {
            "id": str(mapping.id),
            "hash": mapping.mapping_hash,
            "column_mapping": mapping.column_mapping,
            "required_variables": mapping.required_variables,
            "normalization_rules": mapping.normalization_rules,
            "duplicate_policy": mapping.duplicate_policy,
        }
        template_snapshot = {
            "template_version_id": str(template.id),
            "template_id": str(template.template_id),
            "version": template.version,
            "status": template.status,
            "input_schema": template.input_schema,
            "prompt_blocks": template.prompt_blocks,
            "structure_blocks": template.structure_blocks,
            "quality_rules": template.quality_rules,
            "channel_config": template.channel_config,
            "policy_snapshot": template.policy_snapshot,
            "content_hash": template.content_hash,
            "policy_hash": template.policy_hash,
        }
        brand_snapshot = dict(campaign.brand_snapshot or {})
        model_policy_snapshot = {
            "campaign_generation_policy": campaign.generation_policy_snapshot,
            "template_policy": template.policy_snapshot,
        }
        quality_policy_snapshot = {
            "template_quality_rules": template.quality_rules,
            "campaign_quality_policy": campaign_quality_policy,
        }
        approval_policy_snapshot = dict(campaign.approval_policy_snapshot)
        publishing_policy_snapshot = {
            "allowed_channels": campaign.channels,
            "campaign_policy": campaign.generation_policy_snapshot.get(
                "publishing", {}
            ),
        }
        retry_policy_snapshot = {
            "max_row_attempts": data.max_row_attempts,
            "retryable_error_codes": campaign.generation_policy_snapshot.get(
                "retryable_error_codes", []
            ),
        }
        job = BulkJob(
            workspace_id=principal.workspace_id,
            campaign_id=data.campaign_id,
            input_file_id=input_file.id,
            mapping_id=mapping.id,
            template_version_id=template.id,
            requested_by=principal.subject_id,
            operation=data.operation.value,
            state=JobState.QUEUED.value,
            priority=data.priority.value,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            input_snapshot_hash=input_file.content_hash,
            mapping_snapshot=mapping_snapshot,
            mapping_snapshot_hash=canonical_hash(mapping_snapshot),
            template_snapshot=template_snapshot,
            template_snapshot_hash=canonical_hash(template_snapshot),
            brand_snapshot=brand_snapshot,
            brand_snapshot_hash=canonical_hash(brand_snapshot),
            model_policy_snapshot=model_policy_snapshot,
            model_policy_hash=canonical_hash(model_policy_snapshot),
            quality_policy_snapshot=quality_policy_snapshot,
            quality_policy_hash=canonical_hash(quality_policy_snapshot),
            approval_policy_snapshot=approval_policy_snapshot,
            approval_policy_hash=canonical_hash(approval_policy_snapshot),
            publishing_policy_snapshot=publishing_policy_snapshot,
            publishing_policy_hash=canonical_hash(publishing_policy_snapshot),
            retry_policy_snapshot=retry_policy_snapshot,
            concurrency_policy_snapshot={
                "entitlement_snapshot": entitlements,
                "requested_concurrency": data.requested_concurrency,
                "requested_daily_throughput": data.requested_daily_throughput,
                "campaign_budget_limits": campaign.budget_limits,
                "campaign_budget_enforcement": campaign.budget_enforcement,
            },
            callback_endpoint_ref=data.callback_endpoint_ref,
            callback_secret_ref=data.callback_secret_ref,
            dry_run=data.dry_run,
            sample_size=data.sample_size,
            total_rows=input_file.row_count,
            estimated_cost=data.estimated_cost,
            maximum_cost=data.maximum_cost,
            authorized_cost=reservation.authorized_amount,
            held_cost=Decimal("0"),
            currency=data.currency.upper(),
            budget_reservation_ref=reservation.reservation_ref,
            max_row_attempts=data.max_row_attempts,
            concurrency_limit=min(data.requested_concurrency, concurrency_max),
            daily_throughput_limit=min(data.requested_daily_throughput, daily_max),
        )
        self._session.add(job)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="bulk.job.queued",
            aggregate_type="bulk_job",
            aggregate_id=job.id,
            details={
                "total_rows": job.total_rows,
                "dry_run": job.dry_run,
                "estimated_cost": str(job.estimated_cost),
            },
        )
        return job, True

    async def get_job(self, principal: Principal, job_id: UUID) -> BulkJob:
        await self._scope(principal.workspace_id)
        return await self._job(principal.workspace_id, job_id)

    async def list_jobs(
        self,
        principal: Principal,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> list[BulkJob]:
        await self._scope(principal.workspace_id)
        query = select(BulkJob).where(BulkJob.workspace_id == principal.workspace_id)
        if state:
            query = query.where(BulkJob.state == state)
        return list(
            await self._session.scalars(
                query.order_by(BulkJob.created_at.desc()).limit(limit).offset(offset)
            )
        )

    async def materialize_csv_snapshot(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        content: bytes,
    ) -> int:
        """Create deterministic rows from the exact immutable CSV bytes."""

        await self._scope(workspace_id)
        job = await self._job(workspace_id, job_id, for_update=True)
        current = JobState(job.state)
        if (
            current in TERMINAL_JOB_STATES
            or current is JobState.CANCEL_REQUESTED
            or job.pause_requested
            or job.budget_kill_switch_triggered
        ):
            return 0
        if current not in {
            JobState.QUEUED,
            JobState.VALIDATING,
            JobState.WAITING_INPUT,
        }:
            raise AppError(
                "BULK_JOB_NOT_MATERIALIZABLE",
                "현재 상태에서 입력 Snapshot을 행으로 변환할 수 없습니다.",
                409,
            )
        input_file = await self._session.scalar(
            select(BulkInputFile).where(
                BulkInputFile.workspace_id == workspace_id,
                BulkInputFile.id == job.input_file_id,
            )
        )
        if input_file is None:
            raise _not_found("BULK_INPUT", "대량 입력 Snapshot")
        validate_private_object_ref(
            input_file.object_ref,
            workspace_id=str(workspace_id),
            namespace="bulk",
        )
        if (
            input_file.input_kind != BulkInputKind.CSV.value
            or input_file.malware_scan_status != "CLEAN"
            or not input_file.malware_scanner
            or not input_file.malware_scanner_version
            or not input_file.malware_scan_result_hash
            or input_file.metadata_json.get("trusted_ingestion")
            != _TRUSTED_INGESTION_VERSION
        ):
            raise AppError(
                "BULK_CSV_SNAPSHOT_UNVERIFIED",
                "검증 완료된 CSV Snapshot만 서버에서 행으로 변환할 수 있습니다.",
                409,
            )
        content_hash = hashlib.sha256(content).hexdigest()
        if (
            len(content) != input_file.size_bytes
            or content_hash != input_file.content_hash
            or content_hash != job.input_snapshot_hash
        ):
            raise AppError(
                "BULK_INPUT_SNAPSHOT_MISMATCH",
                "실행 시점의 입력 바이트가 고정된 Snapshot과 일치하지 않습니다.",
                409,
            )
        if canonical_hash(job.mapping_snapshot) != job.mapping_snapshot_hash:
            raise AppError(
                "BULK_MAPPING_SNAPSHOT_MISMATCH",
                "고정된 변수 Mapping Snapshot의 무결성을 확인할 수 없습니다.",
                409,
            )

        target_rows = (
            job.sample_size if job.dry_run and job.sample_size else job.total_rows
        )
        if target_rows is None or target_rows < 1 or target_rows > input_file.row_count:
            raise AppError(
                "BULK_MATERIALIZATION_TARGET_INVALID",
                "행 변환 대상 수가 고정된 입력 범위와 일치하지 않습니다.",
                409,
            )
        existing_rows = list(
            await self._session.scalars(
                select(BulkRow)
                .where(
                    BulkRow.workspace_id == workspace_id,
                    BulkRow.job_id == job.id,
                )
                .order_by(BulkRow.row_no)
                .with_for_update()
            )
        )
        if len(existing_rows) > target_rows:
            raise AppError(
                "BULK_ROW_SNAPSHOT_MISMATCH",
                "기존 행이 고정된 실행 범위를 벗어났습니다.",
                409,
            )
        existing_by_number = {value.row_no: value for value in existing_rows}
        if len(existing_by_number) != len(existing_rows):
            raise AppError(
                "BULK_ROW_SNAPSHOT_MISMATCH",
                "기존 행 번호의 무결성을 확인할 수 없습니다.",
                409,
            )

        required = tuple(str(value) for value in job.mapping_snapshot.get("required_variables", []))
        column_mapping = {
            str(column): str(variable)
            for column, variable in job.mapping_snapshot.get("column_mapping", {}).items()
        }
        exact_action = str(
            job.mapping_snapshot.get("duplicate_policy", {}).get("exact_action", "REVIEW")
        )
        normalized_total = Decimal(job.estimated_cost).quantize(
            _COST_QUANTUM,
            rounding=ROUND_DOWN,
        )
        total_units = int(normalized_total / _COST_QUANTUM)
        base_units, remainder_units = divmod(total_units, target_rows)
        first_row_by_hash: dict[str, UUID] = {}
        expected_numbers: set[int] = set()
        created_count = 0
        parsed_count = 0
        for normalized in iter_normalized_csv_rows(
            content,
            encoding=input_file.encoding,
            delimiter=input_file.delimiter,
            header_row=input_file.header_row,
        ):
            parsed_count += 1
            if normalized.row_no > target_rows:
                continue
            expected_numbers.add(normalized.row_no)
            source_values = dict(normalized.values)
            sanitized = {
                variable: source_values.get(column)
                for column, variable in column_mapping.items()
            }
            digest = canonical_hash(sanitized)
            deterministic_id = uuid5(
                job.id,
                f"{normalized.row_no}:{normalized.input_hash}",
            )
            idempotency_key = canonical_hash(
                {
                    "input_hash": normalized.input_hash,
                    "job_id": str(job.id),
                    "row_no": normalized.row_no,
                    "snapshot_hash": job.input_snapshot_hash,
                }
            )
            duplicate_of_row_id = first_row_by_hash.get(digest)
            first_row_by_hash.setdefault(digest, deterministic_id)
            errors = [
                {"path": variable, "reason": "required"}
                for variable in required
                if not str(sanitized.get(variable, "")).strip()
            ]
            if errors:
                state = BulkRowState.INVALID
            elif duplicate_of_row_id is not None:
                state = BulkRowState.DUPLICATE
            else:
                state = BulkRowState.READY
            row_units = base_units + (
                1 if normalized.row_no <= remainder_units else 0
            )
            estimated_cost = _COST_QUANTUM * row_units
            existing = existing_by_number.get(normalized.row_no)
            if existing is not None:
                if (
                    existing.id != deterministic_id
                    or existing.row_idempotency_key != idempotency_key
                    or existing.input_hash != digest
                    or existing.input_json != sanitized
                    or existing.validation_errors != errors
                    or existing.duplicate_of_row_id != duplicate_of_row_id
                    or existing.duplicate_action
                    != (exact_action if duplicate_of_row_id else None)
                    or Decimal(existing.estimated_cost) != estimated_cost
                ):
                    raise AppError(
                        "BULK_ROW_SNAPSHOT_MISMATCH",
                        "기존 행이 불변 입력 Snapshot의 결정적 결과와 일치하지 않습니다.",
                        409,
                        remediation={"row_no": normalized.row_no},
                    )
                continue
            row = BulkRow(
                id=deterministic_id,
                workspace_id=workspace_id,
                job_id=job.id,
                row_no=normalized.row_no,
                row_idempotency_key=idempotency_key,
                input_hash=digest,
                input_json=sanitized,
                state=state.value,
                validation_errors=errors,
                duplicate_of_row_id=duplicate_of_row_id,
                duplicate_action=exact_action if duplicate_of_row_id else None,
                estimated_cost=estimated_cost,
            )
            self._session.add(row)
            created_count += 1
        if parsed_count != input_file.row_count or len(expected_numbers) != target_rows:
            raise AppError(
                "BULK_INPUT_ROW_COUNT_MISMATCH",
                "입력 Snapshot의 실제 행 수가 검증된 Metadata와 일치하지 않습니다.",
                409,
            )
        if set(existing_by_number) != expected_numbers.intersection(existing_by_number):
            raise AppError(
                "BULK_ROW_SNAPSHOT_MISMATCH",
                "기존 행이 고정된 입력 Snapshot에 포함되지 않습니다.",
                409,
            )
        await self._session.flush()
        await self._refresh_progress(job)
        if created_count:
            worker = Principal(
                subject_id=job.requested_by,
                workspace_id=workspace_id,
                session_id=None,
                permissions=frozenset(),
                authentication_method="worker",
            )
            await self._record(
                principal=worker,
                action="bulk.rows.materialized",
                aggregate_type="bulk_job",
                aggregate_id=job.id,
                details={
                    "created_rows": created_count,
                    "snapshot_hash": job.input_snapshot_hash,
                    "target_rows": target_rows,
                },
            )
        return created_count

    async def list_rows(
        self,
        principal: Principal,
        job_id: UUID,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> list[BulkRow]:
        await self._scope(principal.workspace_id)
        await self._job(principal.workspace_id, job_id)
        query = select(BulkRow).where(
            BulkRow.workspace_id == principal.workspace_id,
            BulkRow.job_id == job_id,
        )
        if state:
            query = query.where(BulkRow.state == state)
        return list(
            await self._session.scalars(
                query.order_by(BulkRow.row_no).limit(limit).offset(offset)
            )
        )

    async def pause_job(
        self, principal: Principal, job_id: UUID, data: BulkCommandRequest
    ) -> BulkJob:
        job = await self._commandable_job(principal, job_id)
        if job.pause_requested:
            await self._append_command(principal, job, BulkCommandKind.PAUSE, data)
            return job
        job.pause_requested = True
        job.pause_requested_at = datetime.now(UTC)
        job.paused_by = principal.subject_id
        await self._append_command(principal, job, BulkCommandKind.PAUSE, data)
        return job

    async def resume_job(
        self, principal: Principal, job_id: UUID, data: BulkCommandRequest
    ) -> BulkJob:
        job = await self._commandable_job(principal, job_id)
        if job.budget_kill_switch_triggered:
            raise AppError(
                "BULK_BUDGET_KILL_ACTIVE",
                "예산 Kill Switch가 활성화된 작업은 재개할 수 없습니다.",
                409,
            )
        job.pause_requested = False
        job.pause_requested_at = None
        job.paused_by = None
        await self._append_command(principal, job, BulkCommandKind.RESUME, data)
        return job

    async def cancel_job(
        self, principal: Principal, job_id: UUID, data: BulkCommandRequest
    ) -> BulkJob:
        job = await self._commandable_job(principal, job_id)
        current = JobState(job.state)
        if current == JobState.CANCEL_REQUESTED:
            await self._append_command(principal, job, BulkCommandKind.CANCEL, data)
            return job
        ensure_job_transition(current, JobState.CANCEL_REQUESTED)
        job.state = JobState.CANCEL_REQUESTED.value
        job.cancel_requested_at = datetime.now(UTC)
        job.cancelled_by = principal.subject_id
        job.pause_requested = True
        await self._append_command(principal, job, BulkCommandKind.CANCEL, data)
        return job

    async def _commandable_job(self, principal: Principal, job_id: UUID) -> BulkJob:
        await self._scope(principal.workspace_id)
        job = await self._job(principal.workspace_id, job_id, for_update=True)
        if JobState(job.state) in TERMINAL_JOB_STATES:
            raise AppError("BULK_JOB_TERMINAL", "완료된 대량 작업은 변경할 수 없습니다.", 409)
        self._require_job_controller(principal, job)
        return job

    @staticmethod
    def _require_job_controller(principal: Principal, job: BulkJob) -> None:
        if (
            job.requested_by != principal.subject_id
            and "bulk:manage" not in principal.permissions
        ):
            raise AppError(
                "BULK_JOB_CONTROL_DENIED",
                "다른 사용자의 대량 작업을 제어할 권한이 없습니다.",
                403,
            )

    async def _append_command(
        self,
        principal: Principal,
        job: BulkJob,
        kind: BulkCommandKind,
        data: BulkCommandRequest | BulkRowsCommand,
        *,
        details: dict[str, Any] | None = None,
    ) -> BulkJobCommand:
        normalized_details = details or {}
        existing = await self._session.scalar(
            select(BulkJobCommand).where(
                BulkJobCommand.workspace_id == principal.workspace_id,
                BulkJobCommand.job_id == job.id,
                BulkJobCommand.actor_id == principal.subject_id,
                BulkJobCommand.kind == kind.value,
                BulkJobCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            _assert_command_matches(existing, data.reason, normalized_details)
            return existing
        command = BulkJobCommand(
            workspace_id=principal.workspace_id,
            job_id=job.id,
            actor_id=principal.subject_id,
            kind=kind.value,
            idempotency_key=data.idempotency_key,
            reason=data.reason,
            details_json=normalized_details,
        )
        self._session.add(command)
        await self._session.flush()
        await self._record(
            principal=principal,
            action=f"bulk.job.{kind.value.casefold()}",
            aggregate_type="bulk_job",
            aggregate_id=job.id,
            details=normalized_details,
        )
        return command

    async def retry_rows(
        self,
        principal: Principal,
        job_id: UUID,
        data: BulkRowsCommand,
        *,
        regenerate: bool,
    ) -> list[BulkRow]:
        await self._scope(principal.workspace_id)
        job = await self._job(principal.workspace_id, job_id, for_update=True)
        self._require_job_controller(principal, job)
        command_kind = (
            BulkCommandKind.REGENERATE_ROWS if regenerate else BulkCommandKind.RETRY_ROWS
        )
        command_details = {"row_ids": [str(value) for value in data.row_ids]}
        existing_command = await self._session.scalar(
            select(BulkJobCommand).where(
                BulkJobCommand.workspace_id == principal.workspace_id,
                BulkJobCommand.job_id == job.id,
                BulkJobCommand.actor_id == principal.subject_id,
                BulkJobCommand.kind == command_kind.value,
                BulkJobCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing_command is not None:
            _assert_command_matches(existing_command, data.reason, command_details)
            return list(
                await self._session.scalars(
                    select(BulkRow)
                    .where(
                        BulkRow.workspace_id == principal.workspace_id,
                        BulkRow.job_id == job.id,
                        BulkRow.id.in_(data.row_ids),
                    )
                    .order_by(BulkRow.row_no)
                )
            )
        if (
            JobState(job.state) in TERMINAL_JOB_STATES
            or job.pause_requested
            or job.budget_kill_switch_triggered
        ):
            raise AppError("BULK_JOB_NOT_RETRYABLE", "현재 대량 작업에서 행을 재시도할 수 없습니다.", 409)
        rows = list(
            await self._session.scalars(
                select(BulkRow)
                .where(
                    BulkRow.workspace_id == principal.workspace_id,
                    BulkRow.job_id == job.id,
                    BulkRow.id.in_(data.row_ids),
                )
                .with_for_update()
            )
        )
        if len(rows) != len(data.row_ids):
            raise _not_found("BULK_ROW", "대량 작업 행")
        for row in rows:
            if row.state == BulkRowState.APPROVED.value or row.approved_at is not None:
                raise AppError(
                    "BULK_APPROVED_ROW_PRESERVED",
                    "승인된 행은 선택 재생성 또는 재시도 대상이 될 수 없습니다.",
                    409,
                )
            if BulkRowState(row.state) not in RETRYABLE_ROW_STATES:
                raise AppError("BULK_ROW_NOT_RETRYABLE", "재시도 가능한 상태가 아닌 행이 포함되었습니다.", 409)
            if row.hard_blocked:
                raise AppError("BULK_ROW_HARD_BLOCKED", "정책 Hard Block 행은 재시도할 수 없습니다.", 409)
            if row.attempt >= job.max_row_attempts:
                raise AppError("BULK_RETRY_LIMIT_REACHED", "행별 최대 재시도 횟수에 도달했습니다.", 409)
            row.state = BulkRowState.QUEUED.value
            row.next_retry_at = None
            row.last_error_code = None
            row.last_error_detail = None
            if regenerate:
                row.generation_job_id = None
                row.content_id = None
                row.content_version_id = None
                row.content_hash = None
                row.quality_assessment_id = None
                row.quality_passed = None
                row.approval_request_id = None
                row.approved_content_hash = None
        await self._append_command(
            principal,
            job,
            command_kind,
            data,
            details=command_details,
        )
        return rows

    async def approve_rows(
        self, principal: Principal, job_id: UUID, data: BulkRowsCommand
    ) -> list[BulkRow]:
        await self._scope(principal.workspace_id)
        job = await self._job(principal.workspace_id, job_id, for_update=True)
        command_details = {"row_ids": [str(value) for value in data.row_ids]}
        existing_command = await self._session.scalar(
            select(BulkJobCommand).where(
                BulkJobCommand.workspace_id == principal.workspace_id,
                BulkJobCommand.job_id == job.id,
                BulkJobCommand.actor_id == principal.subject_id,
                BulkJobCommand.kind == BulkCommandKind.APPROVE_ROWS.value,
                BulkJobCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing_command is not None:
            _assert_command_matches(existing_command, data.reason, command_details)
            return list(
                await self._session.scalars(
                    select(BulkRow)
                    .where(
                        BulkRow.workspace_id == principal.workspace_id,
                        BulkRow.job_id == job.id,
                        BulkRow.id.in_(data.row_ids),
                    )
                    .order_by(BulkRow.row_no)
                )
            )
        rows = list(
            await self._session.scalars(
                select(BulkRow)
                .where(
                    BulkRow.workspace_id == principal.workspace_id,
                    BulkRow.job_id == job.id,
                    BulkRow.id.in_(data.row_ids),
                )
                .with_for_update()
            )
        )
        if len(rows) != len(data.row_ids):
            raise _not_found("BULK_ROW", "대량 작업 행")
        for row in rows:
            if row.hard_blocked or row.quality_passed is not True:
                raise AppError(
                    "BULK_ROW_APPROVAL_BLOCKED",
                    "정책 위험 또는 품질 미달 행은 일괄 승인에서 제외해야 합니다.",
                    409,
                    remediation={"row_id": str(row.id)},
                )
            if not row.approval_request_id or not row.content_hash:
                raise AppError(
                    "BULK_ROW_APPROVAL_EVIDENCE_MISSING",
                    "행별 승인 요청과 정확한 콘텐츠 버전이 필요합니다.",
                    409,
                )
            approval = await self._session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workspace_id == principal.workspace_id,
                    ApprovalRequest.id == row.approval_request_id,
                )
            )
            if (
                approval is None
                or approval.status != ApprovalRequestStatus.APPROVED.value
                or approval.approved_content_hash != row.content_hash
            ):
                raise AppError(
                    "BULK_ROW_APPROVAL_STALE",
                    "승인 결과가 현재 행의 콘텐츠 Hash와 일치하지 않습니다.",
                    409,
                )
            row.state = BulkRowState.APPROVED.value
            row.approved_content_hash = row.content_hash
            row.approved_by = principal.subject_id
            row.approved_at = datetime.now(UTC)
        await self._append_command(
            principal,
            job,
            BulkCommandKind.APPROVE_ROWS,
            data,
            details=command_details,
        )
        await self._refresh_progress(job)
        return rows

    async def authorize_next_row(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        row_id: UUID,
        next_estimated_cost: Decimal,
    ) -> bool:
        """Worker kill-switch hook called immediately before a row is dequeued."""

        await self._scope(workspace_id)
        job = await self._job(workspace_id, job_id, for_update=True)
        row = await self._session.scalar(
            select(BulkRow)
            .where(
                BulkRow.workspace_id == workspace_id,
                BulkRow.job_id == job.id,
                BulkRow.id == row_id,
            )
            .with_for_update()
        )
        if row is None:
            raise _not_found("BULK_ROW", "대량 작업 행")
        if next_estimated_cost != row.estimated_cost:
            raise AppError(
                "BULK_BUDGET_ESTIMATE_MISMATCH",
                "고정된 행별 예상 원가와 다른 Hold 금액은 사용할 수 없습니다.",
                409,
            )
        if job.pause_requested or job.budget_kill_switch_triggered:
            return False
        if JobState(job.state) in TERMINAL_JOB_STATES or job.state == JobState.CANCEL_REQUESTED.value:
            return False
        decision = evaluate_budget_boundary(
            finalized_cost=job.actual_cost,
            held_cost=job.held_cost,
            next_estimated_cost=next_estimated_cost,
            maximum_cost=min(job.maximum_cost, job.authorized_cost),
        )
        if decision.kill_switch:
            job.budget_kill_switch_triggered = True
            job.pause_requested = True
            job.pause_requested_at = datetime.now(UTC)
            job.error_code = "BUDGET_KILL_SWITCH"
            job.error_detail = decision.reason
            return False
        if row.state not in {BulkRowState.READY.value, BulkRowState.QUEUED.value}:
            return False
        job.held_cost += next_estimated_cost
        row.state = BulkRowState.PROCESSING.value
        return True

    async def record_row_attempt(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        row_id: UUID,
        outcome: BulkAttemptOutcome,
        actual_cost: Decimal,
        started_at: datetime,
        completed_at: datetime,
        generation_job_id: UUID | None = None,
        content_id: UUID | None = None,
        content_version_id: UUID | None = None,
        content_hash: str | None = None,
        quality_assessment_id: UUID | None = None,
        quality_passed: bool | None = None,
        approval_request_id: UUID | None = None,
        spam_similarity_score: Decimal | None = None,
        value_score: Decimal | None = None,
        risk_findings: list[dict[str, Any]] | None = None,
        hard_blocked: bool = False,
        quality_snapshot: dict[str, Any] | None = None,
        approval_snapshot: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        retryable: bool = False,
    ) -> BulkRowAttempt:
        await self._scope(workspace_id)
        job = await self._job(workspace_id, job_id, for_update=True)
        row = await self._session.scalar(
            select(BulkRow)
            .where(
                BulkRow.workspace_id == workspace_id,
                BulkRow.job_id == job.id,
                BulkRow.id == row_id,
            )
            .with_for_update()
        )
        if row is None:
            raise _not_found("BULK_ROW", "대량 작업 행")
        if actual_cost < 0:
            raise AppError("BULK_COST_INVALID", "행 원가는 음수일 수 없습니다.", 422)
        if row.approved_at is not None:
            raise AppError("BULK_APPROVED_ROW_PRESERVED", "승인된 행 결과는 변경할 수 없습니다.", 409)
        attempt_number = row.attempt + 1
        if attempt_number > job.max_row_attempts:
            raise AppError("BULK_RETRY_LIMIT_REACHED", "행별 최대 시도 횟수를 초과했습니다.", 409)
        exact_content = (content_id, content_version_id, content_hash)
        if any(value is None for value in exact_content) and any(
            value is not None for value in exact_content
        ):
            raise AppError(
                "BULK_CONTENT_IDENTITY_INCOMPLETE",
                "콘텐츠 ID·버전·Hash는 함께 기록해야 합니다.",
                422,
            )
        maximum_similarity, minimum_value = _spam_policy_thresholds(
            job.quality_policy_snapshot.get("campaign_quality_policy")
        )
        spam_decision = evaluate_spam_gate(
            similarity_score=spam_similarity_score,
            value_score=value_score,
            maximum_similarity=maximum_similarity,
            minimum_value=minimum_value,
        )
        assessment = None
        if quality_assessment_id is not None:
            assessment = await self._session.scalar(
                select(QualityAssessment).where(
                    QualityAssessment.workspace_id == workspace_id,
                    QualityAssessment.id == quality_assessment_id,
                    QualityAssessment.content_id == content_id,
                    QualityAssessment.content_version_id == content_version_id,
                    QualityAssessment.content_hash == content_hash,
                )
            )
            if assessment is None:
                raise AppError(
                    "BULK_QUALITY_EVIDENCE_INVALID",
                    "품질 평가가 정확한 콘텐츠 버전과 일치하지 않습니다.",
                    409,
                )
            server_quality_passed = (
                assessment.decision == AssessmentDecision.PASS.value
                and not assessment.blocking_policy_event_ids
                and not assessment.non_overrideable_policy_event_ids
            )
            if quality_passed is not None and quality_passed != server_quality_passed:
                raise AppError(
                    "BULK_QUALITY_DECISION_MISMATCH",
                    "전달된 품질 결과가 서버의 품질 평가 증거와 일치하지 않습니다.",
                    409,
                )
            quality_passed = server_quality_passed
            hard_blocked = hard_blocked or bool(
                assessment.non_overrideable_policy_event_ids
            )
            quality_snapshot = {
                "assessment_id": str(assessment.id),
                "assessment_hash": assessment.assessment_hash,
                "decision": assessment.decision,
                "total_score": str(assessment.total_score),
                "blocking_policy_event_ids": assessment.blocking_policy_event_ids,
                "non_overrideable_policy_event_ids": (
                    assessment.non_overrideable_policy_event_ids
                ),
            }
        elif quality_passed is not None or quality_snapshot is not None:
            raise AppError(
                "BULK_QUALITY_EVIDENCE_UNTRUSTED",
                "품질 평가는 서버에 저장된 평가 ID로만 증명할 수 있습니다.",
                409,
            )
        approval = None
        if approval_request_id is not None:
            approval = await self._session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workspace_id == workspace_id,
                    ApprovalRequest.id == approval_request_id,
                    ApprovalRequest.content_id == content_id,
                    ApprovalRequest.content_version_id == content_version_id,
                    ApprovalRequest.content_hash == content_hash,
                )
            )
            if approval is None or approval.status in {
                ApprovalRequestStatus.CHANGES_REQUESTED.value,
                ApprovalRequestStatus.REJECTED.value,
                ApprovalRequestStatus.EXPIRED.value,
                ApprovalRequestStatus.INVALIDATED.value,
                ApprovalRequestStatus.SUPERSEDED.value,
            }:
                raise AppError(
                    "BULK_APPROVAL_EVIDENCE_INVALID",
                    "승인 요청이 정확한 콘텐츠 버전의 유효한 상태가 아닙니다.",
                    409,
                )
            if (
                approval.status == ApprovalRequestStatus.APPROVED.value
                and approval.approved_content_hash != content_hash
            ):
                raise AppError(
                    "BULK_APPROVAL_HASH_MISMATCH",
                    "승인된 콘텐츠 Hash가 행 결과와 일치하지 않습니다.",
                    409,
                )
            approval_snapshot = {
                "approval_request_id": str(approval.id),
                "status": approval.status,
                "approved_content_hash": approval.approved_content_hash,
                "approval_stages_hash": approval.approval_stages_hash,
            }
        elif approval_snapshot is not None:
            raise AppError(
                "BULK_APPROVAL_EVIDENCE_UNTRUSTED",
                "승인 결과는 서버에 저장된 승인 요청 ID로만 증명할 수 있습니다.",
                409,
            )
        normalized_findings = list(risk_findings or [])
        effective_outcome = outcome
        if outcome is BulkAttemptOutcome.SUCCEEDED:
            if any(value is None for value in exact_content):
                raise AppError(
                    "BULK_CONTENT_EVIDENCE_MISSING",
                    "성공 결과에는 정확한 콘텐츠 버전이 필요합니다.",
                    409,
                )
            if quality_assessment_id is None or approval_request_id is None:
                raise AppError(
                    "BULK_REVIEW_EVIDENCE_MISSING",
                    "성공 결과에는 품질 평가와 승인 요청이 필요합니다.",
                    409,
                )
            if quality_passed is not True or not spam_decision.auto_publish_allowed:
                effective_outcome = BulkAttemptOutcome.QUALITY_BLOCKED
        if hard_blocked:
            effective_outcome = BulkAttemptOutcome.QUALITY_BLOCKED
        attempt = BulkRowAttempt(
            workspace_id=workspace_id,
            job_id=job.id,
            row_id=row.id,
            attempt_number=attempt_number,
            input_hash=row.input_hash,
            outcome=effective_outcome.value,
            generation_job_id=generation_job_id,
            content_id=content_id,
            content_version_id=content_version_id,
            content_hash=content_hash,
            quality_snapshot=quality_snapshot,
            approval_snapshot=approval_snapshot,
            actual_cost=actual_cost,
            error_code=error_code,
            error_detail=error_detail,
            retryable=retryable,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._session.add(attempt)
        row.attempt = attempt_number
        row.actual_cost += actual_cost
        row.generation_job_id = generation_job_id
        row.content_id = content_id
        row.content_version_id = content_version_id
        row.content_hash = content_hash
        row.quality_assessment_id = quality_assessment_id
        row.quality_passed = quality_passed
        row.approval_request_id = approval_request_id
        row.spam_similarity_score = spam_similarity_score
        row.value_score = value_score
        row.risk_findings = normalized_findings + [
            {"kind": "BULK_SPAM_GATE", "reason": reason}
            for reason in spam_decision.reasons
        ]
        row.hard_blocked = hard_blocked
        row.last_error_code = error_code
        row.last_error_detail = error_detail
        succeeded_state = (
            BulkRowState.APPROVED.value
            if approval is not None
            and approval.status == ApprovalRequestStatus.APPROVED.value
            else BulkRowState.WAITING_REVIEW.value
        )
        row.state = {
            BulkAttemptOutcome.SUCCEEDED: succeeded_state,
            BulkAttemptOutcome.QUALITY_BLOCKED: BulkRowState.QUALITY_BLOCKED.value,
            BulkAttemptOutcome.RETRYABLE_FAILED: BulkRowState.RETRYABLE_FAILED.value,
            BulkAttemptOutcome.FINAL_FAILED: BulkRowState.FINAL_FAILED.value,
            BulkAttemptOutcome.CANCELLED: BulkRowState.CANCELLED.value,
        }[effective_outcome]
        if row.state == BulkRowState.APPROVED.value and approval is not None:
            row.approved_content_hash = approval.approved_content_hash
            row.approved_by = approval.approved_by
            row.approved_at = approval.approved_at
        job.actual_cost += actual_cost
        job.held_cost = max(Decimal("0"), job.held_cost - row.estimated_cost)
        if job.actual_cost + job.held_cost >= min(job.maximum_cost, job.authorized_cost):
            job.budget_kill_switch_triggered = True
            job.pause_requested = True
            job.pause_requested_at = datetime.now(UTC)
        await self._session.flush()
        await self._refresh_progress(job)
        return attempt

    async def _refresh_progress(self, job: BulkJob) -> None:
        statement = select(
            func.count(BulkRow.id).filter(BulkRow.state.in_(_PROCESSED_ROW_STATES)),
            func.count(BulkRow.id).filter(
                BulkRow.state.in_(
                    {BulkRowState.SUCCEEDED.value, BulkRowState.APPROVED.value}
                )
            ),
            func.count(BulkRow.id).filter(
                BulkRow.state.in_(
                    {
                        BulkRowState.DUPLICATE.value,
                        BulkRowState.WAITING_REVIEW.value,
                        BulkRowState.QUALITY_BLOCKED.value,
                    }
                )
            ),
            func.count(BulkRow.id).filter(
                BulkRow.state.in_(
                    {
                        BulkRowState.INVALID.value,
                        BulkRowState.RETRYABLE_FAILED.value,
                        BulkRowState.FINAL_FAILED.value,
                    }
                )
            ),
            func.count(BulkRow.id).filter(
                BulkRow.state == BulkRowState.CANCELLED.value
            ),
        ).where(
            BulkRow.workspace_id == job.workspace_id,
            BulkRow.job_id == job.id,
        )
        summary = (await self._session.execute(statement)).one()
        (
            job.processed_rows,
            job.succeeded_rows,
            job.review_rows,
            job.failed_rows,
            job.cancelled_rows,
        ) = (int(value or 0) for value in summary)
        target_rows = job.sample_size if job.dry_run and job.sample_size else job.total_rows
        job.progress_percent = min(
            Decimal("100"),
            (
                Decimal(job.processed_rows * 100) / Decimal(target_rows)
                if target_rows
                else Decimal("0")
            ),
        )

    async def create_schedule(
        self, principal: Principal, data: BulkScheduleCreate
    ) -> BulkSchedule:
        await self._scope(principal.workspace_id)
        for model, identifier, code, label in (
            (BulkInputFile, data.input_file_id, "BULK_INPUT", "대량 입력 Snapshot"),
            (BulkMapping, data.mapping_id, "BULK_MAPPING", "대량 변수 Mapping"),
            (TemplateVersion, data.template_version_id, "TEMPLATE_VERSION", "템플릿 버전"),
        ):
            exists = await self._session.scalar(
                select(model.id).where(
                    model.workspace_id == principal.workspace_id,
                    model.id == identifier,
                )
            )
            if exists is None:
                raise _not_found(code, label)
        value = BulkSchedule(
            workspace_id=principal.workspace_id,
            input_file_id=data.input_file_id,
            mapping_id=data.mapping_id,
            template_version_id=data.template_version_id,
            timezone=data.timezone,
            schedule_expression=data.schedule_expression,
            next_run_at=data.next_run_at,
            config_snapshot=data.config_snapshot,
            config_snapshot_hash=canonical_hash(data.config_snapshot),
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="bulk.schedule.created",
            aggregate_type="bulk_schedule",
            aggregate_id=value.id,
            details={"next_run_at": value.next_run_at.isoformat()},
        )
        return value

    async def request_export(
        self,
        principal: Principal,
        job_id: UUID,
        *,
        export_kind: str,
        include_states: list[str],
        idempotency_key: str,
    ) -> UUID:
        await self._scope(principal.workspace_id)
        job = await self._job(principal.workspace_id, job_id)
        details = {"export_kind": export_kind, "include_states": include_states}
        existing = await self._session.scalar(
            select(BulkJobCommand).where(
                BulkJobCommand.workspace_id == principal.workspace_id,
                BulkJobCommand.job_id == job.id,
                BulkJobCommand.actor_id == principal.subject_id,
                BulkJobCommand.kind == BulkCommandKind.EXPORT.value,
                BulkJobCommand.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            _assert_command_matches(existing, "export requested", details)
            return existing.id
        command = BulkJobCommand(
            workspace_id=principal.workspace_id,
            job_id=job.id,
            actor_id=principal.subject_id,
            kind=BulkCommandKind.EXPORT.value,
            idempotency_key=idempotency_key,
            reason="export requested",
            details_json=details,
        )
        self._session.add(command)
        await self._session.flush()
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type="bulk_job",
            aggregate_id=str(job.id),
            event_type="bulk.export.requested",
            schema_version=_OUTBOX_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "job_id": str(job.id),
                "actor_id": str(principal.subject_id),
                "export_kind": export_kind,
                "include_states": include_states,
                "idempotency_key": idempotency_key,
            },
        )
        return command.id


def _spam_policy_thresholds(value: object) -> tuple[Decimal, Decimal]:
    if not isinstance(value, dict):
        raise AppError(
            "BULK_SPAM_POLICY_INVALID",
            "Campaign의 Spam 품질 정책을 확인할 수 없습니다.",
            503,
        )
    spam = value.get("spam")
    if not isinstance(spam, dict) or not {
        "maximum_similarity",
        "minimum_value",
    }.issubset(spam):
        raise AppError(
            "BULK_SPAM_POLICY_INVALID",
            "Campaign의 Spam 품질 기준이 완전하지 않습니다.",
            503,
        )
    try:
        maximum_similarity = Decimal(str(spam["maximum_similarity"]))
        minimum_value = Decimal(str(spam["minimum_value"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AppError(
            "BULK_SPAM_POLICY_INVALID",
            "Campaign의 Spam 품질 기준 형식이 올바르지 않습니다.",
            503,
        ) from exc
    if (
        not maximum_similarity.is_finite()
        or not minimum_value.is_finite()
        or not Decimal("0") <= maximum_similarity <= Decimal("1")
        or not Decimal("0") <= minimum_value <= Decimal("100")
    ):
        raise AppError(
            "BULK_SPAM_POLICY_INVALID",
            "Campaign의 Spam 품질 기준 범위가 올바르지 않습니다.",
            503,
        )
    return maximum_similarity, minimum_value

def _is_sha256(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _required_positive_entitlement(values: dict[str, Any], key: str) -> int:
    raw = values.get(key)
    if isinstance(raw, bool):
        raw = None
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "BULK_ENTITLEMENT_INCOMPLETE",
            "대량 작업 플랜 한도를 확인할 수 없습니다.",
            503,
            fields=[{"path": f"entitlements.{key}", "reason": "required positive integer"}],
        ) from exc
    if parsed < 1:
        raise AppError(
            "BULK_ENTITLEMENT_INCOMPLETE",
            "대량 작업 플랜 한도를 확인할 수 없습니다.",
            503,
            fields=[{"path": f"entitlements.{key}", "reason": "required positive integer"}],
        )
    return parsed


def _assert_command_matches(
    command: BulkJobCommand,
    reason: str,
    details: dict[str, Any],
) -> None:
    if command.reason != reason or command.details_json != details:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "같은 명령 멱등키가 다른 요청에 사용되었습니다.",
            409,
        )


def _not_found(code: str, label: str) -> AppError:
    return AppError(
        code=f"{code}_NOT_FOUND",
        message=f"{label}을(를) 찾을 수 없습니다.",
        status_code=404,
    )
