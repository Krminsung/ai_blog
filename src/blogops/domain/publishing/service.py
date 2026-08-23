"""Request-side publishing service: validate, snapshot and enqueue durable work only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.publishing.enums import (
    ConnectionOperation,
    ConnectionState,
    ConflictAction,
    NaverChecklistState,
    PublishOperation,
    PublishedPostState,
    PublishingProvider,
    PublishVisibility,
    SagaStepKind,
)
from blogops.domain.publishing.models import (
    NaverChecklistEvent,
    NaverManualConfirmation,
    NaverPolicyAcknowledgement,
    NaverPublishPackage,
    PublicationPolicy,
    PublishedPost,
    PublishingConnection,
    PublishingConnectionJob,
    PublishingNotification,
    PublishAttempt,
    PublishJob,
    PublishQuotaUsage,
    PublishSagaStep,
    RemotePostSnapshot,
)
from blogops.domain.publishing.references import (
    FailClosedPublishingEntitlementResolver,
    PublishReadyContent,
    PublishingEntitlementResolver,
    PublishingReadinessResolver,
)
from blogops.domain.publishing.rendering import (
    naver_image_manifest,
    package_diff,
    render_for_cms,
)
from blogops.domain.publishing.repository import PublishingRepository
from blogops.domain.publishing.rules import (
    NAVER_MANUAL_POLICY_NOTICE,
    NAVER_MANUAL_POLICY_VERSION,
    canonical_hash,
    quota_for,
    redact_metadata,
    validate_naver_post,
    validate_schedule,
)
from blogops.domain.publishing.schemas import (
    CancelPublishCreate,
    ConnectionCommandCreate,
    NaverChecklistUpdate,
    NaverManualConfirm,
    NaverPackageCreate,
    PublicationPolicyCreate,
    PublishedPostDelete,
    PublishedPostUpdate,
    PublishingConnectionCreate,
    PublishCreate,
    PublishPreviewCreate,
    ReconcileCreate,
    RetryPublishCreate,
    RollbackCreate,
)
from blogops.domain.publishing.security import validate_secret_ref, validate_site_url
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class DurableJobCreation:
    job: PublishJob
    created: bool


@dataclass(frozen=True, slots=True)
class ConnectionJobCreation:
    job: PublishingConnectionJob
    created: bool


class PublishingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        readiness: PublishingReadinessResolver,
        entitlements: PublishingEntitlementResolver | None = None,
    ) -> None:
        self.session = session
        self.repo = PublishingRepository(session)
        self.readiness = readiness
        self.entitlements = entitlements or FailClosedPublishingEntitlementResolver()

    async def create_policy(
        self, principal: Principal, data: PublicationPolicyCreate
    ) -> PublicationPolicy:
        await self._scope(principal.workspace_id)
        latest = int(
            await self.session.scalar(
                select(func.coalesce(func.max(PublicationPolicy.version), 0)).where(
                    PublicationPolicy.workspace_id == principal.workspace_id
                )
            )
            or 0
        )
        _assert_expected_version("publishing_policy", data.expected_previous_version, latest)
        snapshot = {
            "version": latest + 1,
            "daily_quotas": dict(sorted(data.daily_quotas.items())),
            "max_schedule_days": data.max_schedule_days,
            "allowed_providers": sorted(item.value for item in data.allowed_providers),
            "require_media_license": data.require_media_license,
            "allowed_custom_contracts": sorted(data.allowed_custom_contracts),
            "naver_policy_version": data.naver_policy_version,
        }
        policy = PublicationPolicy(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            version=latest + 1,
            daily_quotas=snapshot["daily_quotas"],
            max_schedule_days=data.max_schedule_days,
            allowed_providers=snapshot["allowed_providers"],
            require_media_license=data.require_media_license,
            allowed_custom_contracts=snapshot["allowed_custom_contracts"],
            naver_policy_version=data.naver_policy_version,
            snapshot_json=snapshot,
            snapshot_hash=canonical_hash(snapshot),
            created_by=principal.subject_id,
        )
        self.session.add(policy)
        await self.repo.flush("publishing_policy")
        await self._record_change(
            principal,
            action="publishing.policy.created",
            target_type="publishing_policy",
            target_id=policy.id,
            details={"version": policy.version, "snapshot_hash": policy.snapshot_hash},
        )
        return policy

    async def list_policies(self, principal: Principal) -> list[PublicationPolicy]:
        return list(
            await self.session.scalars(
                select(PublicationPolicy)
                .where(PublicationPolicy.workspace_id == principal.workspace_id)
                .order_by(PublicationPolicy.version.desc())
            )
        )

    async def create_connection(
        self, principal: Principal, data: PublishingConnectionCreate
    ) -> PublishingConnection:
        await self._scope(principal.workspace_id)
        policy = await self.repo.latest_policy(principal.workspace_id)
        if data.provider.value not in set(policy.allowed_providers):
            raise AppError(
                "PUBLISH_PROVIDER_NOT_ALLOWED",
                "워크스페이스 게시 정책에서 허용하지 않은 provider입니다.",
                403,
            )
        if (
            data.provider is PublishingProvider.CUSTOMER_CMS
            and data.official_contract not in set(policy.allowed_custom_contracts)
        ):
            raise AppError(
                "CUSTOMER_CMS_CONTRACT_NOT_ALLOWED",
                "검토되어 허용 목록에 등록된 공식 고객 CMS 계약만 연결할 수 있습니다.",
                422,
            )
        await self._assert_connection_limit(principal.workspace_id)
        safe_site = validate_site_url(data.site_url)
        _validate_timezone(data.site_timezone)
        secret_ref = validate_secret_ref(data.credential_secret_ref)
        connection = PublishingConnection(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            provider=data.provider.value,
            name=data.name,
            site_url=safe_site.normalized,
            site_timezone=data.site_timezone,
            remote_site_id=data.remote_site_id,
            official_contract=data.official_contract,
            api_version=data.api_version,
            api_deprecation_at=data.api_deprecation_at,
            credential_secret_ref=secret_ref,
            state=ConnectionState.PENDING.value,
            capabilities=[],
            safe_config_json=data.safe_config,
            site_settings_snapshot={},
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(connection)
        await self.repo.flush("publishing_connection")
        await self._record_change(
            principal,
            action="publishing.connection.created",
            target_type="publishing_connection",
            target_id=connection.id,
            details={
                "provider": connection.provider,
                "official_contract": connection.official_contract,
                "site_hostname": safe_site.hostname,
                "credential_ref_present": True,
            },
        )
        return connection

    async def get_connection(
        self, principal: Principal, connection_id: UUID
    ) -> PublishingConnection:
        return await self.repo.connection(principal.workspace_id, connection_id)

    async def list_connections(
        self,
        principal: Principal,
        *,
        provider: PublishingProvider | None,
        state: ConnectionState | None,
    ) -> list[PublishingConnection]:
        query = select(PublishingConnection).where(
            PublishingConnection.workspace_id == principal.workspace_id
        )
        if provider is not None:
            query = query.where(PublishingConnection.provider == provider.value)
        if state is not None:
            query = query.where(PublishingConnection.state == state.value)
        return list(
            await self.session.scalars(
                query.order_by(PublishingConnection.created_at.desc(), PublishingConnection.id)
            )
        )

    async def create_connection_job(
        self,
        principal: Principal,
        connection_id: UUID,
        operation: ConnectionOperation,
        data: ConnectionCommandCreate,
    ) -> ConnectionJobCreation:
        request_hash = canonical_hash(
            {
                "connection_id": str(connection_id),
                "operation": operation.value,
                "expected_lock_version": data.expected_lock_version,
            }
        )
        existing = await self.session.scalar(
            select(PublishingConnectionJob).where(
                PublishingConnectionJob.workspace_id == principal.workspace_id,
                PublishingConnectionJob.requested_by == principal.subject_id,
                PublishingConnectionJob.operation == operation.value,
                PublishingConnectionJob.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            _assert_same_request(existing.request_hash, request_hash)
            return ConnectionJobCreation(existing, False)
        connection = await self.repo.connection(
            principal.workspace_id, connection_id, for_update=True
        )
        _assert_lock("publishing_connection", data.expected_lock_version, connection.lock_version)
        if connection.state == ConnectionState.DISCONNECTED.value:
            raise AppError(
                "PUBLISH_CONNECTION_DISCONNECTED",
                "연결 해제된 채널에는 이 작업을 요청할 수 없습니다.",
                409,
            )
        job = PublishingConnectionJob(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            connection_id=connection.id,
            requested_by=principal.subject_id,
            operation=operation.value,
            state=JobState.QUEUED.value,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            expected_lock_version=data.expected_lock_version,
            attempt=0,
            max_attempts=3,
            checks_json=[],
        )
        self.session.add(job)
        await self.repo.flush("publishing_connection_job")
        await self._record_change(
            principal,
            action="publishing.connection.job_queued",
            target_type="publishing_connection_job",
            target_id=job.id,
            details={"connection_id": str(connection.id), "operation": operation.value},
        )
        return ConnectionJobCreation(job, True)

    async def get_connection_job(
        self, principal: Principal, job_id: UUID
    ) -> PublishingConnectionJob:
        return await self.repo.connection_job(principal.workspace_id, job_id)

    async def list_connection_jobs(
        self,
        principal: Principal,
        *,
        connection_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[PublishingConnectionJob]:
        query = select(PublishingConnectionJob).where(
            PublishingConnectionJob.workspace_id == principal.workspace_id
        )
        if connection_id is not None:
            query = query.where(PublishingConnectionJob.connection_id == connection_id)
        return list(
            await self.session.scalars(
                query.order_by(
                    PublishingConnectionJob.created_at.desc(),
                    PublishingConnectionJob.id,
                )
                .limit(limit)
                .offset(offset)
            )
        )

    async def create_publish_job(
        self,
        principal: Principal,
        content_id: UUID,
        data: PublishCreate,
        *,
        idempotency_key: str,
    ) -> DurableJobCreation:
        request_hash = canonical_hash(
            {"content_id": str(content_id), "request": data.model_dump(mode="json")}
        )
        existing = await self._existing_publish_job(
            principal,
            operation=PublishOperation.CREATE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return DurableJobCreation(existing, False)
        connection = await self.repo.connection(
            principal.workspace_id, data.connection_id
        )
        _assert_connection_active(connection)
        _assert_publish_capability(connection, data.visibility)
        policy = await self.repo.latest_policy(principal.workspace_id)
        self._assert_provider_policy(policy, connection)
        schedule = _schedule_for(connection, policy, data)
        ready = await self._ready(
            principal.workspace_id,
            content_id=content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            approval_request_id=data.approval_request_id,
            connection=connection,
            policy=policy,
        )
        _validate_options(data.options.model_dump(mode="json"), connection)
        _validate_featured_media(data.options.model_dump(mode="json"), ready)
        input_snapshot = {
            "content_id": str(content_id),
            "content_version_id": str(ready.content_version_id),
            "content_hash": ready.content_hash,
            "approval_request_id": str(ready.approval_request_id),
            "approved_by": str(ready.approved_by),
            "connection_id": str(connection.id),
            "channel": ready.channel,
            "visibility": data.visibility.value,
            "scheduled_at_utc": schedule["scheduled_at_utc"],
            "scheduled_local": schedule["scheduled_local"],
            "site_timezone": connection.site_timezone,
            "dst_fold": schedule["dst_fold"],
            "options": data.options.model_dump(mode="json"),
            "media_manifest": _media_manifest(ready),
        }
        return await self._create_job(
            principal,
            operation=PublishOperation.CREATE,
            ready=ready,
            connection=connection,
            policy=policy,
            target_post=None,
            visibility=data.visibility,
            schedule=schedule,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot=input_snapshot,
        )

    async def preview_publish(
        self,
        principal: Principal,
        content_id: UUID,
        data: PublishPreviewCreate,
    ) -> dict[str, Any]:
        connection = await self.repo.connection(
            principal.workspace_id, data.connection_id
        )
        _assert_connection_active(connection)
        policy = await self.repo.latest_policy(principal.workspace_id)
        self._assert_provider_policy(policy, connection)
        options = data.options.model_dump(mode="json")
        _validate_options(options, connection)
        ready = await self._ready(
            principal.workspace_id,
            content_id=content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            approval_request_id=data.approval_request_id,
            connection=connection,
            policy=policy,
        )
        _validate_featured_media(options, ready)
        rendered = render_for_cms(
            ready.document,
            tracking=dict(options.get("tracking") or {}),
            attributions=[
                item.attribution_text
                for item in ready.media
                if item.attribution_text
            ],
        )
        return {
            "content_id": ready.content_id,
            "content_version_id": ready.content_version_id,
            "content_hash": ready.content_hash,
            "connection_id": connection.id,
            "provider": connection.provider,
            "title": ready.title,
            "html": rendered.html,
            "blocks": rendered.blocks,
            "render_hash": rendered.render_hash,
            "media_manifest": _public_media_manifest(ready),
            "unsupported_blocks": rendered.unsupported,
            "unsupported_options": _unsupported_preview_options(
                connection.provider, options
            ),
            "approximation_notice": (
                "공식 API에 전송될 안전 HTML의 근사 미리보기이며 실제 테마·플러그인·"
                "채널 렌더링과 차이가 날 수 있습니다."
            ),
        }

    async def update_published_post(
        self,
        principal: Principal,
        post_id: UUID,
        data: PublishedPostUpdate,
        *,
        idempotency_key: str,
    ) -> DurableJobCreation:
        request_hash = canonical_hash(
            {"post_id": str(post_id), "request": data.model_dump(mode="json")}
        )
        existing = await self._existing_publish_job(
            principal,
            operation=PublishOperation.UPDATE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return DurableJobCreation(existing, False)
        post = await self.repo.published_post(principal.workspace_id, post_id)
        _assert_lock("published_post", data.expected_lock_version, post.lock_version)
        _assert_remote_expectations(
            post,
            expected_etag=data.expected_remote_etag,
            expected_hash=data.expected_remote_hash,
            expected_updated_at=data.expected_remote_updated_at,
        )
        if post.connection_id is None:
            raise AppError(
                "MANUAL_POST_UPDATE_PACKAGE_REQUIRED",
                "네이버 수동 게시물은 수정 패키지를 새로 생성해야 합니다.",
                409,
            )
        connection = await self.repo.connection(principal.workspace_id, post.connection_id)
        _assert_connection_active(connection)
        _assert_publish_capability(connection, data.visibility)
        if "update" not in set(connection.capabilities):
            raise AppError("PUBLISH_UPDATE_PERMISSION_MISSING", "연결에 원격 수정 권한이 없습니다.", 403)
        policy = await self.repo.latest_policy(principal.workspace_id)
        self._assert_provider_policy(policy, connection)
        schedule = _schedule_for(connection, policy, data)
        ready = await self._ready(
            principal.workspace_id,
            content_id=post.content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            approval_request_id=data.approval_request_id,
            connection=connection,
            policy=policy,
        )
        _validate_options(data.options.model_dump(mode="json"), connection)
        _validate_featured_media(data.options.model_dump(mode="json"), ready)
        snapshot = {
            "published_post_id": str(post.id),
            "expected_remote_etag": data.expected_remote_etag,
            "expected_remote_hash": data.expected_remote_hash,
            "expected_remote_updated_at": (
                data.expected_remote_updated_at.isoformat()
                if data.expected_remote_updated_at
                else None
            ),
            "conflict_action": data.conflict_action.value,
            "content_version_id": str(ready.content_version_id),
            "content_hash": ready.content_hash,
            "channel": ready.channel,
            "approval_request_id": str(ready.approval_request_id),
            "approved_by": str(ready.approved_by),
            "visibility": data.visibility.value,
            "scheduled_at_utc": schedule["scheduled_at_utc"],
            "scheduled_local": schedule["scheduled_local"],
            "site_timezone": connection.site_timezone,
            "dst_fold": schedule["dst_fold"],
            "options": data.options.model_dump(mode="json"),
            "media_manifest": _media_manifest(ready),
        }
        return await self._create_job(
            principal,
            operation=PublishOperation.UPDATE,
            ready=ready,
            connection=connection,
            policy=policy,
            target_post=post,
            visibility=data.visibility,
            schedule=schedule,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot=snapshot,
        )

    async def delete_published_post(
        self,
        principal: Principal,
        post_id: UUID,
        data: PublishedPostDelete,
        *,
        idempotency_key: str,
    ) -> DurableJobCreation:
        request_hash = canonical_hash(
            {"post_id": str(post_id), "request": data.model_dump(mode="json")}
        )
        existing = await self._existing_publish_job(
            principal,
            operation=PublishOperation.DELETE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return DurableJobCreation(existing, False)
        post = await self.repo.published_post(principal.workspace_id, post_id)
        _assert_lock("published_post", data.expected_lock_version, post.lock_version)
        if post.remote_id != data.confirm_remote_id:
            raise AppError(
                "PUBLISH_DELETE_CONFIRMATION_MISMATCH",
                "삭제 확인용 원격 Post ID가 일치하지 않습니다.",
                409,
            )
        _assert_remote_expectations(
            post,
            expected_etag=data.expected_remote_etag,
            expected_hash=data.expected_remote_hash,
            expected_updated_at=None,
        )
        if post.connection_id is None:
            raise AppError(
                "MANUAL_POST_DELETE_UNSUPPORTED",
                "수동 게시물은 원격에서 사용자가 직접 삭제해야 합니다.",
                409,
            )
        connection = await self.repo.connection(principal.workspace_id, post.connection_id)
        if "delete" not in set(connection.capabilities):
            raise AppError("PUBLISH_DELETE_PERMISSION_MISSING", "연결에 원격 삭제 권한이 없습니다.", 403)
        return await self._existing_post_job(
            principal,
            post,
            operation=PublishOperation.DELETE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot={
                "published_post_id": str(post.id),
                "confirm_remote_id": data.confirm_remote_id,
                "expected_remote_etag": data.expected_remote_etag,
                "expected_remote_hash": data.expected_remote_hash,
                "force_delete": data.force_delete,
                "conflict_action": ConflictAction.ABORT.value,
            },
        )

    async def reconcile_published_post(
        self,
        principal: Principal,
        post_id: UUID,
        data: ReconcileCreate,
        *,
        idempotency_key: str,
    ) -> DurableJobCreation:
        request_hash = canonical_hash(
            {"post_id": str(post_id), "request": data.model_dump(mode="json")}
        )
        existing = await self._existing_publish_job(
            principal,
            operation=PublishOperation.RECONCILE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return DurableJobCreation(existing, False)
        post = await self.repo.published_post(principal.workspace_id, post_id)
        _assert_lock("published_post", data.expected_lock_version, post.lock_version)
        if post.connection_id is None:
            raise AppError("MANUAL_RECONCILE_UNSUPPORTED", "수동 게시물은 자동 동기화하지 않습니다.", 409)
        return await self._existing_post_job(
            principal,
            post,
            operation=PublishOperation.RECONCILE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot={
                "published_post_id": str(post.id),
                "expected_remote_etag": post.remote_etag,
                "expected_remote_hash": post.remote_hash,
                "expected_remote_updated_at": (
                    post.remote_updated_at.isoformat() if post.remote_updated_at else None
                ),
                "conflict_action": data.conflict_action.value,
            },
        )

    async def rollback_published_post(
        self,
        principal: Principal,
        post_id: UUID,
        data: RollbackCreate,
        *,
        idempotency_key: str,
    ) -> DurableJobCreation:
        request_hash = canonical_hash(
            {"post_id": str(post_id), "request": data.model_dump(mode="json")}
        )
        existing = await self._existing_publish_job(
            principal,
            operation=PublishOperation.ROLLBACK,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return DurableJobCreation(existing, False)
        post = await self.repo.published_post(principal.workspace_id, post_id)
        _assert_lock("published_post", data.expected_lock_version, post.lock_version)
        _assert_remote_expectations(
            post,
            expected_etag=data.expected_remote_etag,
            expected_hash=data.expected_remote_hash,
            expected_updated_at=None,
        )
        snapshot = await self.repo.remote_snapshot(principal.workspace_id, data.snapshot_id)
        if snapshot.published_post_id != post.id:
            raise AppError("ROLLBACK_SNAPSHOT_POST_MISMATCH", "다른 게시물의 스냅샷은 복구할 수 없습니다.", 422)
        if post.connection_id is None:
            raise AppError("MANUAL_ROLLBACK_UNSUPPORTED", "수동 게시물은 자동 복구하지 않습니다.", 409)
        return await self._existing_post_job(
            principal,
            post,
            operation=PublishOperation.ROLLBACK,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot={
                "published_post_id": str(post.id),
                "snapshot_id": str(snapshot.id),
                "snapshot_hash": snapshot.snapshot_hash,
                "expected_remote_etag": data.expected_remote_etag,
                "expected_remote_hash": data.expected_remote_hash,
                "conflict_action": ConflictAction.ABORT.value,
            },
        )

    async def cancel_publish_job(
        self, principal: Principal, job_id: UUID, data: CancelPublishCreate
    ) -> PublishJob:
        job = await self.repo.publish_job(
            principal.workspace_id, job_id, for_update=True
        )
        _assert_lock("publish_job", data.expected_lock_version, job.lock_version)
        cancellable = (
            job.state == JobState.SCHEDULED.value and job.started_at is None
        )
        if job.state in {
            JobState.RETRYABLE_FAILED.value,
            JobState.SUCCEEDED.value,
        } and job.target_published_post_id is not None:
            post = await self.repo.published_post(
                principal.workspace_id, job.target_published_post_id
            )
            cancellable = post.state == PublishedPostState.SCHEDULED.value
        if not cancellable:
            raise AppError(
                "PUBLISH_JOB_NOT_CANCELLABLE",
                "대기 중이거나 원격 예약 상태인 게시 작업만 취소할 수 있습니다.",
                409,
            )
        job.state = JobState.CANCEL_REQUESTED.value
        job.cancel_requested_at = datetime.now(UTC)
        job.result_json = {"cancellation_reason": data.reason}
        await self.repo.flush("publish_job")
        await self._record_change(
            principal,
            action="publishing.job.cancel_requested",
            target_type="publish_job",
            target_id=job.id,
            details={"reason": data.reason},
        )
        return job

    async def retry_publish_job(
        self, principal: Principal, job_id: UUID, data: RetryPublishCreate
    ) -> PublishJob:
        job = await self.repo.publish_job(
            principal.workspace_id, job_id, for_update=True
        )
        _assert_lock("publish_job", data.expected_lock_version, job.lock_version)
        if job.state not in {
            JobState.PARTIAL.value,
            JobState.RETRYABLE_FAILED.value,
        }:
            raise AppError(
                "PUBLISH_JOB_NOT_RETRYABLE",
                "부분 성공 또는 재시도 가능 실패 작업만 다시 실행할 수 있습니다.",
                409,
            )
        if job.state == JobState.PARTIAL.value:
            failures = (job.result_json or {}).get("media_failures", [])
            retryable_classes = {
                "NETWORK",
                "RATE_LIMIT",
                "SERVER",
            }
            if not any(
                isinstance(item, dict)
                and item.get("retry_class") in retryable_classes
                for item in failures
            ):
                raise AppError(
                    "PUBLISH_PARTIAL_FAILURE_NOT_RETRYABLE",
                    "네트워크·429·5xx에 해당하는 부분 실패만 다시 시도할 수 있습니다.",
                    409,
                )
        if job.attempt >= job.max_attempts:
            raise AppError(
                "PUBLISH_MAX_ATTEMPTS_REACHED",
                "게시 작업의 최대 시도 횟수에 도달했습니다.",
                409,
            )
        job.state = JobState.SCHEDULED.value
        job.finished_at = None
        job.retry_after_seconds = None
        job.error_code = None
        job.error_detail = None
        job.result_json = {"retry_reason": data.reason}
        await self.repo.flush("publish_job")
        await self._record_change(
            principal,
            action="publishing.job.retry_scheduled",
            target_type="publish_job",
            target_id=job.id,
            details={"reason": data.reason, "next_attempt": job.attempt + 1},
        )
        return job

    async def get_publish_job(self, principal: Principal, job_id: UUID) -> PublishJob:
        return await self.repo.publish_job(principal.workspace_id, job_id)

    async def list_publish_jobs(
        self,
        principal: Principal,
        *,
        content_id: UUID | None,
        state: JobState | None,
        limit: int,
        offset: int,
    ) -> list[PublishJob]:
        query = select(PublishJob).where(PublishJob.workspace_id == principal.workspace_id)
        if content_id is not None:
            query = query.where(PublishJob.content_id == content_id)
        if state is not None:
            query = query.where(PublishJob.state == state.value)
        return list(
            await self.session.scalars(
                query.order_by(PublishJob.created_at.desc(), PublishJob.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def publish_job_steps(
        self, principal: Principal, job_id: UUID
    ) -> list[PublishSagaStep]:
        await self.repo.publish_job(principal.workspace_id, job_id)
        return list(
            await self.session.scalars(
                select(PublishSagaStep)
                .where(
                    PublishSagaStep.workspace_id == principal.workspace_id,
                    PublishSagaStep.job_id == job_id,
                )
                .order_by(PublishSagaStep.sequence)
            )
        )

    async def publish_job_attempts(
        self, principal: Principal, job_id: UUID
    ) -> list[PublishAttempt]:
        await self.repo.publish_job(principal.workspace_id, job_id)
        return list(
            await self.session.scalars(
                select(PublishAttempt)
                .where(
                    PublishAttempt.workspace_id == principal.workspace_id,
                    PublishAttempt.job_id == job_id,
                )
                .order_by(PublishAttempt.created_at, PublishAttempt.id)
            )
        )

    async def get_published_post(
        self, principal: Principal, post_id: UUID
    ) -> PublishedPost:
        return await self.repo.published_post(principal.workspace_id, post_id)

    async def list_published_posts(
        self,
        principal: Principal,
        *,
        content_id: UUID | None,
        provider: PublishingProvider | None,
        limit: int,
        offset: int,
    ) -> list[PublishedPost]:
        query = select(PublishedPost).where(
            PublishedPost.workspace_id == principal.workspace_id
        )
        if content_id is not None:
            query = query.where(PublishedPost.content_id == content_id)
        if provider is not None:
            query = query.where(PublishedPost.provider == provider.value)
        return list(
            await self.session.scalars(
                query.order_by(PublishedPost.updated_at.desc(), PublishedPost.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def list_remote_snapshots(
        self, principal: Principal, post_id: UUID
    ) -> list[RemotePostSnapshot]:
        await self.repo.published_post(principal.workspace_id, post_id)
        return list(
            await self.session.scalars(
                select(RemotePostSnapshot)
                .where(
                    RemotePostSnapshot.workspace_id == principal.workspace_id,
                    RemotePostSnapshot.published_post_id == post_id,
                )
                .order_by(RemotePostSnapshot.captured_at.desc())
            )
        )

    async def create_naver_package(
        self,
        principal: Principal,
        content_id: UUID,
        data: NaverPackageCreate,
    ) -> NaverPublishPackage:
        policy = await self.repo.latest_policy(principal.workspace_id)
        if PublishingProvider.NAVER_MANUAL.value not in set(policy.allowed_providers):
            raise AppError("NAVER_MANUAL_NOT_ALLOWED", "워크스페이스 게시 정책에서 네이버 수동 패키지를 허용하지 않습니다.", 403)
        expected_policy = policy.naver_policy_version
        if expected_policy != NAVER_MANUAL_POLICY_VERSION:
            raise AppError(
                "NAVER_POLICY_CONFIGURATION_STALE",
                "서버의 최신 네이버 수동 게시 정책 버전을 설정에 반영해야 합니다.",
                409,
            )
        if data.acknowledged_policy_version != expected_policy:
            raise AppError(
                "NAVER_POLICY_ACK_REQUIRED",
                "최신 수동 게시 정책 버전에 대한 사용자 확인이 필요합니다.",
                409,
            )
        ready = await self.readiness.resolve(
            workspace_id=principal.workspace_id,
            content_id=content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            approval_request_id=data.approval_request_id,
            channel=PublishingProvider.NAVER_MANUAL.value,
            require_media_license=policy.require_media_license,
        )
        rendered = render_for_cms(
            ready.document,
            attributions=[
                item.attribution_text
                for item in ready.media
                if item.attribution_text
            ],
        )
        images = naver_image_manifest(ready.media)
        previous: NaverPublishPackage | None = None
        if data.previous_package_id is not None:
            previous = await self.repo.naver_package(
                principal.workspace_id, data.previous_package_id
            )
            _assert_naver_package_integrity(previous)
            if previous.content_id != content_id:
                raise AppError("NAVER_PREVIOUS_PACKAGE_MISMATCH", "같은 콘텐츠 패키지만 수정 기준으로 사용할 수 있습니다.", 422)
        diff = package_diff(
            previous.formatted_blocks if previous else None,
            rendered.blocks,
            previous.image_manifest if previous else None,
            images,
        )
        checklist = [
            {"key": "content_reviewed", "label": "제목과 본문을 직접 검토함", "required": True},
            {"key": "images_inserted", "label": "Manifest 순서대로 이미지를 삽입함", "required": bool(images)},
            {"key": "disclosures_checked", "label": "광고·AI·출처 고지를 확인함", "required": True},
            {"key": "links_checked", "label": "외부 링크와 추적 파라미터를 확인함", "required": True},
            {"key": "tags_checked", "label": "태그를 직접 확인함", "required": True},
            {"key": "manual_publish", "label": "최종 게시 버튼은 사용자가 직접 누름", "required": True},
        ]
        notice_hash = canonical_hash(
            {"version": expected_policy, "notice": NAVER_MANUAL_POLICY_NOTICE}
        )
        package_payload = {
            "content_version_id": str(ready.content_version_id),
            "content_hash": ready.content_hash,
            "approval_snapshot_hash": ready.approval_snapshot_hash,
            "title": ready.title,
            "blocks": rendered.blocks,
            "images": images,
            "tags": data.tags or ready.tags,
            "checklist": checklist,
            "diff": diff,
            "unsupported": rendered.unsupported,
            "policy_version": expected_policy,
            "policy_notice_hash": notice_hash,
            "app_launch_url": None,
        }
        package = NaverPublishPackage(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content_id,
            content_version_id=ready.content_version_id,
            content_hash=ready.content_hash,
            approval_request_id=ready.approval_request_id,
            approval_snapshot_hash=ready.approval_snapshot_hash,
            previous_package_id=previous.id if previous else None,
            title=ready.title,
            formatted_blocks=rendered.blocks,
            copy_manifest=[
                {
                    "order": item["order"],
                    "block_key": item["block_key"],
                    "copy_text": item["copy_text"],
                }
                for item in rendered.blocks
            ],
            image_manifest=images,
            image_order=[str(item["placement_key"]) for item in images],
            tags=data.tags or ready.tags,
            checklist=checklist,
            diff_manifest=diff,
            unsupported_blocks=rendered.unsupported,
            policy_version=expected_policy,
            policy_notice=NAVER_MANUAL_POLICY_NOTICE,
            policy_notice_hash=notice_hash,
            app_launch_url=None,
            package_hash=canonical_hash(package_payload),
            requested_by=principal.subject_id,
        )
        acknowledgement = await self.session.scalar(
            select(NaverPolicyAcknowledgement).where(
                NaverPolicyAcknowledgement.workspace_id == principal.workspace_id,
                NaverPolicyAcknowledgement.user_id == principal.subject_id,
                NaverPolicyAcknowledgement.policy_version == expected_policy,
            )
        )
        if acknowledgement is not None and acknowledgement.notice_hash != notice_hash:
            raise AppError(
                "NAVER_POLICY_NOTICE_VERSION_MISMATCH",
                "정책 문구가 변경되면 네이버 수동 게시 정책 버전도 변경해야 합니다.",
                409,
            )
        self.session.add(package)
        if acknowledgement is None:
            self.session.add(
                NaverPolicyAcknowledgement(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    package_id=package.id,
                    user_id=principal.subject_id,
                    policy_version=expected_policy,
                    notice_hash=notice_hash,
                )
            )
        if data.reminder_at is not None:
            if data.reminder_at <= datetime.now(UTC):
                raise AppError("NAVER_REMINDER_IN_PAST", "예약 알림은 미래 시각이어야 합니다.", 422)
            if data.reminder_at > datetime.now(UTC) + timedelta(
                days=policy.max_schedule_days
            ):
                raise AppError(
                    "NAVER_REMINDER_TOO_FAR",
                    "게시 정책의 최대 예약 알림 기간을 초과했습니다.",
                    422,
                )
            self.session.add(
                PublishingNotification(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    recipient_id=principal.subject_id,
                    publish_job_id=None,
                    naver_package_id=package.id,
                    notification_type="NAVER_MANUAL_PUBLISH_REMINDER",
                    payload_json={
                        "package_id": str(package.id),
                        "content_id": str(content_id),
                        "content_path": f"/content/{content_id}",
                        "automatic_publish": False,
                    },
                    due_at=data.reminder_at,
                )
            )
        await self.repo.flush("naver_publish_package")
        await self._record_change(
            principal,
            action="publishing.naver.package_created",
            target_type="naver_publish_package",
            target_id=package.id,
            details={
                "content_id": str(content_id),
                "content_version_id": str(package.content_version_id),
                "content_hash": package.content_hash,
                "package_hash": package.package_hash,
                "policy_version": package.policy_version,
                "automatic_publish": False,
                "reminder_at": data.reminder_at.isoformat() if data.reminder_at else None,
            },
        )
        return package

    async def get_naver_package(
        self, principal: Principal, package_id: UUID
    ) -> NaverPublishPackage:
        package = await self.repo.naver_package(principal.workspace_id, package_id)
        _assert_naver_package_integrity(package)
        return package

    async def list_naver_packages(
        self,
        principal: Principal,
        *,
        content_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[NaverPublishPackage]:
        query = select(NaverPublishPackage).where(
            NaverPublishPackage.workspace_id == principal.workspace_id
        )
        if content_id is not None:
            query = query.where(NaverPublishPackage.content_id == content_id)
        packages = list(
            await self.session.scalars(
                query.order_by(
                    NaverPublishPackage.created_at.desc(), NaverPublishPackage.id
                )
                .limit(limit)
                .offset(offset)
            )
        )
        for package in packages:
            _assert_naver_package_integrity(package)
        return packages

    async def list_naver_checklist_events(
        self, principal: Principal, package_id: UUID
    ) -> list[NaverChecklistEvent]:
        package = await self.repo.naver_package(principal.workspace_id, package_id)
        _assert_naver_package_integrity(package)
        return list(
            await self.session.scalars(
                select(NaverChecklistEvent)
                .where(
                    NaverChecklistEvent.workspace_id == principal.workspace_id,
                    NaverChecklistEvent.package_id == package_id,
                )
                .order_by(NaverChecklistEvent.created_at, NaverChecklistEvent.id)
            )
        )

    async def update_naver_checklist(
        self,
        principal: Principal,
        package_id: UUID,
        data: NaverChecklistUpdate,
    ) -> NaverChecklistEvent:
        package = await self.repo.naver_package(principal.workspace_id, package_id)
        _assert_naver_package_integrity(package)
        allowed = {str(item["key"]) for item in package.checklist}
        if data.checklist_key not in allowed:
            raise AppError("NAVER_CHECKLIST_KEY_INVALID", "패키지에 없는 체크리스트 항목입니다.", 422)
        event = NaverChecklistEvent(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            package_id=package.id,
            checklist_key=data.checklist_key,
            state=data.state.value,
            actor_id=principal.subject_id,
        )
        self.session.add(event)
        await self.repo.flush("naver_checklist_event")
        await self._record_change(
            principal,
            action="publishing.naver.checklist_changed",
            target_type="naver_publish_package",
            target_id=package.id,
            details={"checklist_key": data.checklist_key, "state": data.state.value},
        )
        return event

    async def confirm_naver_manual_publish(
        self,
        principal: Principal,
        package_id: UUID,
        data: NaverManualConfirm,
    ) -> NaverManualConfirmation:
        package = await self.repo.naver_package(principal.workspace_id, package_id)
        _assert_naver_package_integrity(package)
        remote_url = validate_naver_post(data.remote_url, data.remote_post_id)
        existing = await self.session.scalar(
            select(NaverManualConfirmation).where(
                NaverManualConfirmation.workspace_id == principal.workspace_id,
                NaverManualConfirmation.package_id == package.id,
            )
        )
        if existing is not None:
            if (
                existing.remote_url != remote_url
                or existing.remote_post_id != data.remote_post_id
            ):
                raise AppError(
                    "NAVER_PACKAGE_ALREADY_CONFIRMED",
                    "패키지가 다른 URL로 이미 확인되었습니다.",
                    409,
                )
            return existing
        policy = await self.repo.latest_policy(principal.workspace_id)
        if (
            PublishingProvider.NAVER_MANUAL.value
            not in set(policy.allowed_providers)
            or policy.naver_policy_version != package.policy_version
        ):
            raise AppError(
                "NAVER_PACKAGE_POLICY_STALE",
                "최신 게시 정책을 확인한 새 네이버 수동 패키지가 필요합니다.",
                409,
            )
        ready = await self.readiness.resolve(
            workspace_id=principal.workspace_id,
            content_id=package.content_id,
            content_version_id=package.content_version_id,
            content_hash=package.content_hash,
            approval_request_id=package.approval_request_id,
            channel=PublishingProvider.NAVER_MANUAL.value,
            require_media_license=policy.require_media_license,
        )
        if ready.approval_snapshot_hash != package.approval_snapshot_hash:
            raise AppError(
                "NAVER_PACKAGE_APPROVAL_STALE",
                "패키지 생성 후 승인 증명이 변경되어 새 패키지가 필요합니다.",
                409,
            )
        await self._assert_naver_checklist_complete(package)
        post = PublishedPost(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=package.content_id,
            content_version_id=package.content_version_id,
            content_hash=package.content_hash,
            approval_request_id=package.approval_request_id,
            connection_id=None,
            created_by_job_id=None,
            naver_package_id=package.id,
            provider=PublishingProvider.NAVER_MANUAL.value,
            remote_site_id=None,
            remote_id=data.remote_post_id,
            remote_url=remote_url,
            state=PublishedPostState.MANUALLY_CONFIRMED.value,
            remote_etag=None,
            remote_hash=canonical_hash(
                {"url": remote_url, "post_id": data.remote_post_id, "manual": True}
            ),
            remote_updated_at=None,
            local_snapshot_hash=package.package_hash,
            last_reconciled_at=datetime.now(UTC),
            lock_version=1,
        )
        confirmation = NaverManualConfirmation(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            package_id=package.id,
            published_post_id=post.id,
            remote_url=remote_url,
            remote_post_id=data.remote_post_id,
            confirmed_by=principal.subject_id,
        )
        self.session.add_all([post, confirmation])
        await self.repo.flush("naver_manual_confirmation")
        await self._record_change(
            principal,
            action="publishing.naver.manual_confirmed",
            target_type="published_post",
            target_id=post.id,
            details={
                "package_id": str(package.id),
                "remote_url": remote_url,
                "remote_post_id": data.remote_post_id,
                "automatic_publish": False,
            },
        )
        return confirmation

    async def list_notifications(
        self,
        principal: Principal,
        *,
        limit: int,
        offset: int,
    ) -> list[PublishingNotification]:
        return list(
            await self.session.scalars(
                select(PublishingNotification)
                .where(
                    PublishingNotification.workspace_id == principal.workspace_id,
                    PublishingNotification.recipient_id == principal.subject_id,
                )
                .order_by(PublishingNotification.due_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    async def _ready(
        self,
        workspace_id: UUID,
        *,
        content_id: UUID,
        content_version_id: UUID,
        content_hash: str,
        approval_request_id: UUID,
        connection: PublishingConnection,
        policy: PublicationPolicy,
    ) -> PublishReadyContent:
        return await self.readiness.resolve(
            workspace_id=workspace_id,
            content_id=content_id,
            content_version_id=content_version_id,
            content_hash=content_hash,
            approval_request_id=approval_request_id,
            channel=connection.provider,
            require_media_license=policy.require_media_license,
        )

    async def _create_job(
        self,
        principal: Principal,
        *,
        operation: PublishOperation,
        ready: PublishReadyContent,
        connection: PublishingConnection,
        policy: PublicationPolicy,
        target_post: PublishedPost | None,
        visibility: PublishVisibility,
        schedule: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
        input_snapshot: dict[str, Any],
        reserve_quota: bool = True,
    ) -> DurableJobCreation:
        if not idempotency_key or len(idempotency_key) > 255:
            raise AppError("IDEMPOTENCY_KEY_REQUIRED", "유효한 Idempotency-Key가 필요합니다.", 422)
        existing = await self.session.scalar(
            select(PublishJob).where(
                PublishJob.workspace_id == principal.workspace_id,
                PublishJob.requested_by == principal.subject_id,
                PublishJob.operation == operation.value,
                PublishJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            _assert_same_request(existing.request_hash, request_hash)
            return DurableJobCreation(existing, False)
        if reserve_quota:
            await self._reserve_quota(
                principal.workspace_id,
                connection=connection,
                policy=policy,
                channel=ready.channel,
                local_day=schedule["local_day"],
            )
        job_id = uuid4()
        job = PublishJob(
            id=job_id,
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            operation=operation.value,
            state=JobState.SCHEDULED.value,
            content_id=ready.content_id,
            content_version_id=ready.content_version_id,
            content_hash=ready.content_hash,
            connection_id=connection.id,
            approval_request_id=ready.approval_request_id,
            approval_snapshot_hash=ready.approval_snapshot_hash,
            policy_id=policy.id,
            policy_snapshot=policy.snapshot_json,
            policy_snapshot_hash=policy.snapshot_hash,
            target_published_post_id=target_post.id if target_post else None,
            visibility=visibility.value,
            scheduled_at_utc=schedule["scheduled_datetime"],
            scheduled_local=schedule["local_datetime"],
            site_timezone=connection.site_timezone,
            dst_fold=schedule["dst_fold"],
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            idempotency_marker=f"blogops-{str(job_id).lower()}",
            input_snapshot=input_snapshot,
            input_snapshot_hash=canonical_hash(input_snapshot),
            attempt=0,
            max_attempts=3,
            lock_version=1,
        )
        self.session.add(job)
        self.session.add_all(_initial_steps(principal.workspace_id, job.id, operation))
        if visibility is PublishVisibility.SCHEDULED:
            recipients = {principal.subject_id, ready.approved_by}
            self.session.add_all(
                PublishingNotification(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    recipient_id=recipient,
                    publish_job_id=job.id,
                    naver_package_id=None,
                    notification_type="PUBLISHING_JOB_SCHEDULED",
                    payload_json={
                        "job_id": str(job.id),
                        "content_id": str(job.content_id),
                        "scheduled_at_utc": schedule["scheduled_at_utc"],
                        "site_timezone": connection.site_timezone,
                    },
                    due_at=datetime.now(UTC),
                )
                for recipient in recipients
            )
        await self.repo.flush("publish_job")
        await self._record_change(
            principal,
            action="publishing.job.scheduled",
            target_type="publish_job",
            target_id=job.id,
            details={
                "operation": operation.value,
                "content_id": str(job.content_id),
                "content_version_id": str(job.content_version_id),
                "content_hash": job.content_hash,
                "connection_id": str(job.connection_id),
                "scheduled_at_utc": (
                    job.scheduled_at_utc.isoformat() if job.scheduled_at_utc else None
                ),
                "request_hash": job.request_hash,
            },
        )
        return DurableJobCreation(job, True)

    async def _existing_post_job(
        self,
        principal: Principal,
        post: PublishedPost,
        *,
        operation: PublishOperation,
        idempotency_key: str,
        request_hash: str,
        input_snapshot: dict[str, Any],
    ) -> DurableJobCreation:
        if post.connection_id is None:
            raise AppError("PUBLISH_CONNECTION_REQUIRED", "자동 원격 작업에는 게시 연결이 필요합니다.", 409)
        connection = await self.repo.connection(principal.workspace_id, post.connection_id)
        _assert_connection_active(connection)
        policy = await self.repo.latest_policy(principal.workspace_id)
        self._assert_provider_policy(policy, connection)
        job_visibility = PublishVisibility.DRAFT
        source_job: PublishJob | None = None
        if (
            operation is PublishOperation.RECONCILE
            and input_snapshot.get("conflict_action") == ConflictAction.OVERWRITE.value
        ):
            ready = await self.readiness.resolve(
                workspace_id=principal.workspace_id,
                content_id=post.content_id,
                content_version_id=post.content_version_id,
                content_hash=post.content_hash,
                approval_request_id=post.approval_request_id,
                channel=connection.provider,
                require_media_license=policy.require_media_license,
            )
            source_job = await self.session.scalar(
                select(PublishJob)
                .where(
                    PublishJob.workspace_id == principal.workspace_id,
                    PublishJob.target_published_post_id == post.id,
                    PublishJob.operation.in_(
                        {PublishOperation.CREATE.value, PublishOperation.UPDATE.value}
                    ),
                    PublishJob.state.in_(
                        {JobState.SUCCEEDED.value, JobState.PARTIAL.value}
                    ),
                )
                .order_by(PublishJob.finished_at.desc(), PublishJob.id.desc())
                .limit(1)
            )
            if source_job is None:
                raise AppError(
                    "PUBLISH_LOCAL_SOURCE_SNAPSHOT_MISSING",
                    "원격 덮어쓰기에 필요한 마지막 로컬 발행 스냅샷이 없습니다.",
                    409,
                )
            job_visibility = PublishVisibility(source_job.visibility)
            input_snapshot = {
                **source_job.input_snapshot,
                **input_snapshot,
                "content_version_id": str(ready.content_version_id),
                "content_hash": ready.content_hash,
                "approval_request_id": str(ready.approval_request_id),
                "approved_by": str(ready.approved_by),
                "channel": ready.channel,
                "media_manifest": _media_manifest(ready),
            }
        else:
            ready = PublishReadyContent(
                content_id=post.content_id,
                content_version_id=post.content_version_id,
                content_hash=post.content_hash,
                title="",
                document=[],
                plain_text="",
                channel=connection.provider,
                language="",
                tags=[],
                approval_request_id=post.approval_request_id,
                approval_snapshot_hash=post.local_snapshot_hash,
                assessment_id=UUID(int=0),
                assessment_hash="",
                quality_config_hash="",
                approved_by=principal.subject_id,
                approved_at=datetime.now(UTC),
                media=(),
            )
        now = datetime.now(UTC)
        if (
            source_job is not None
            and job_visibility is PublishVisibility.SCHEDULED
            and source_job.scheduled_at_utc is not None
            and source_job.scheduled_at_utc > now
            and source_job.scheduled_local is not None
        ):
            schedule = {
                "scheduled_datetime": source_job.scheduled_at_utc,
                "local_datetime": source_job.scheduled_local,
                "scheduled_at_utc": source_job.scheduled_at_utc.isoformat(),
                "scheduled_local": source_job.scheduled_local.isoformat(),
                "dst_fold": source_job.dst_fold,
                "local_day": source_job.scheduled_local.date(),
            }
        else:
            if job_visibility is PublishVisibility.SCHEDULED:
                job_visibility = PublishVisibility.PUBLISH
            local = now.astimezone(ZoneInfo(connection.site_timezone)).replace(tzinfo=None)
            schedule = {
                "scheduled_datetime": now,
                "local_datetime": local,
                "scheduled_at_utc": now.isoformat(),
                "scheduled_local": local.isoformat(),
                "dst_fold": local.fold,
                "local_day": local.date(),
            }
        if source_job is not None:
            input_snapshot = {
                **input_snapshot,
                "visibility": job_visibility.value,
                "scheduled_at_utc": schedule["scheduled_at_utc"],
                "scheduled_local": schedule["scheduled_local"],
                "site_timezone": connection.site_timezone,
                "dst_fold": schedule["dst_fold"],
            }
        return await self._create_job(
            principal,
            operation=operation,
            ready=ready,
            connection=connection,
            policy=policy,
            target_post=post,
            visibility=job_visibility,
            schedule=schedule,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_snapshot=input_snapshot,
            reserve_quota=False,
        )

    async def _existing_publish_job(
        self,
        principal: Principal,
        *,
        operation: PublishOperation,
        idempotency_key: str,
        request_hash: str,
    ) -> PublishJob | None:
        if not idempotency_key or len(idempotency_key) > 255:
            raise AppError(
                "IDEMPOTENCY_KEY_REQUIRED",
                "유효한 Idempotency-Key가 필요합니다.",
                422,
            )
        existing = await self.session.scalar(
            select(PublishJob).where(
                PublishJob.workspace_id == principal.workspace_id,
                PublishJob.requested_by == principal.subject_id,
                PublishJob.operation == operation.value,
                PublishJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            _assert_same_request(existing.request_hash, request_hash)
        return existing

    async def _reserve_quota(
        self,
        workspace_id: UUID,
        *,
        connection: PublishingConnection,
        policy: PublicationPolicy,
        channel: str,
        local_day: date,
    ) -> None:
        quota_limit = quota_for(policy.daily_quotas, connection.provider, channel)
        if quota_limit is None:
            raise AppError(
                "PUBLISH_QUOTA_POLICY_MISSING",
                "provider 또는 채널의 일일 발행 한도가 설정되지 않았습니다.",
                409,
            )
        usage = await self.session.scalar(
            select(PublishQuotaUsage)
            .where(
                PublishQuotaUsage.workspace_id == workspace_id,
                PublishQuotaUsage.connection_id == connection.id,
                PublishQuotaUsage.provider == connection.provider,
                PublishQuotaUsage.channel == channel,
                PublishQuotaUsage.local_day == local_day,
            )
            .with_for_update()
        )
        if usage is None:
            usage = PublishQuotaUsage(
                id=uuid4(),
                workspace_id=workspace_id,
                connection_id=connection.id,
                policy_id=policy.id,
                provider=connection.provider,
                channel=channel,
                local_day=local_day,
                timezone_name=connection.site_timezone,
                quota_limit=quota_limit,
                reserved_count=0,
                completed_count=0,
                lock_version=1,
            )
            self.session.add(usage)
        else:
            usage.policy_id = policy.id
            usage.quota_limit = quota_limit
        if usage.reserved_count >= quota_limit:
            raise AppError(
                "PUBLISH_DAILY_QUOTA_EXCEEDED",
                "사이트 로컬 날짜의 일일 게시 한도를 초과했습니다.",
                429,
                remediation={
                    "local_day": local_day.isoformat(),
                    "site_timezone": connection.site_timezone,
                    "quota_limit": quota_limit,
                },
            )
        usage.reserved_count += 1

    async def _assert_connection_limit(self, workspace_id: UUID) -> None:
        limit = await self.entitlements.max_connections(workspace_id=workspace_id)
        if limit < 1:
            raise AppError(
                "PUBLISH_CONNECTION_ENTITLEMENT_INVALID",
                "게시 연결 Entitlement 한도는 1 이상이어야 합니다.",
                409,
            )
        current = int(
            await self.session.scalar(
                select(func.count(PublishingConnection.id)).where(
                    PublishingConnection.workspace_id == workspace_id,
                    PublishingConnection.state
                    != ConnectionState.DISCONNECTED.value,
                )
            )
            or 0
        )
        if current >= limit:
            raise AppError(
                "PUBLISH_CONNECTION_PLAN_LIMIT",
                "플랜의 활성 게시 연결 수 한도에 도달했습니다.",
                409,
                remediation={"limit": limit, "active_connections": current},
            )

    def _assert_provider_policy(
        self, policy: PublicationPolicy, connection: PublishingConnection
    ) -> None:
        if connection.provider not in set(policy.allowed_providers):
            raise AppError("PUBLISH_PROVIDER_NOT_ALLOWED", "게시 정책에서 허용하지 않은 provider입니다.", 403)
        if (
            connection.provider == PublishingProvider.CUSTOMER_CMS.value
            and connection.official_contract not in set(policy.allowed_custom_contracts)
        ):
            raise AppError("CUSTOMER_CMS_CONTRACT_NOT_ALLOWED", "허용 목록의 공식 고객 CMS 계약만 사용할 수 있습니다.", 403)

    async def _assert_naver_checklist_complete(
        self, package: NaverPublishPackage
    ) -> None:
        events = list(
            await self.session.scalars(
                select(NaverChecklistEvent)
                .where(
                    NaverChecklistEvent.workspace_id == package.workspace_id,
                    NaverChecklistEvent.package_id == package.id,
                )
                .order_by(NaverChecklistEvent.created_at, NaverChecklistEvent.id)
            )
        )
        latest = {item.checklist_key: item.state for item in events}
        missing = [
            str(item["key"])
            for item in package.checklist
            if item.get("required")
            and latest.get(str(item["key"])) != NaverChecklistState.CHECKED.value
        ]
        if missing:
            raise AppError(
                "NAVER_CHECKLIST_INCOMPLETE",
                "수동 게시 완료 전에 필수 체크리스트를 확인해야 합니다.",
                409,
                fields=[{"path": "checklist", "reason": item} for item in missing],
            )

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self.session, workspace_id)

    async def _record_change(
        self,
        principal: Principal,
        *,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        safe_details = redact_metadata(details)
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=safe_details,
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
                **safe_details,
            },
        )


def _initial_steps(
    workspace_id: UUID, job_id: UUID, operation: PublishOperation
) -> list[PublishSagaStep]:
    kinds = {
        PublishOperation.CREATE: [
            SagaStepKind.VALIDATE_READINESS,
            SagaStepKind.UPLOAD_MEDIA,
            SagaStepKind.WRITE_POST,
            SagaStepKind.VERIFY_REMOTE,
            SagaStepKind.SNAPSHOT_REMOTE,
            SagaStepKind.NOTIFY,
        ],
        PublishOperation.UPDATE: [
            SagaStepKind.VALIDATE_READINESS,
            SagaStepKind.FETCH_REMOTE,
            SagaStepKind.SNAPSHOT_REMOTE,
            SagaStepKind.UPLOAD_MEDIA,
            SagaStepKind.WRITE_POST,
            SagaStepKind.VERIFY_REMOTE,
            SagaStepKind.NOTIFY,
        ],
        PublishOperation.DELETE: [
            SagaStepKind.FETCH_REMOTE,
            SagaStepKind.SNAPSHOT_REMOTE,
            SagaStepKind.WRITE_POST,
            SagaStepKind.NOTIFY,
        ],
        PublishOperation.RECONCILE: [
            SagaStepKind.RECONCILE,
            SagaStepKind.SNAPSHOT_REMOTE,
            SagaStepKind.UPLOAD_MEDIA,
            SagaStepKind.WRITE_POST,
            SagaStepKind.VERIFY_REMOTE,
            SagaStepKind.NOTIFY,
        ],
        PublishOperation.ROLLBACK: [
            SagaStepKind.FETCH_REMOTE,
            SagaStepKind.SNAPSHOT_REMOTE,
            SagaStepKind.ROLLBACK,
            SagaStepKind.VERIFY_REMOTE,
            SagaStepKind.NOTIFY,
        ],
    }[operation]
    return [
        PublishSagaStep(
            id=uuid4(),
            workspace_id=workspace_id,
            job_id=job_id,
            sequence=index,
            step_kind=kind.value,
            state=StepState.PENDING.value,
            attempt=0,
            request_metadata={},
            response_metadata={},
            lock_version=1,
        )
        for index, kind in enumerate(kinds, start=1)
    ]


def _schedule_for(
    connection: PublishingConnection,
    policy: PublicationPolicy,
    data: PublishCreate | PublishedPostUpdate,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    if data.visibility is PublishVisibility.SCHEDULED:
        if data.site_timezone != connection.site_timezone:
            raise AppError("PUBLISH_SITE_TIMEZONE_MISMATCH", "요청 시간대가 연결의 진단된 사이트 시간대와 다릅니다.", 422)
        assert data.scheduled_at_utc is not None
        assert data.scheduled_local is not None
        assert data.site_timezone is not None
        validated = validate_schedule(
            scheduled_at_utc=data.scheduled_at_utc,
            scheduled_local=data.scheduled_local,
            timezone_name=data.site_timezone,
            fold=data.dst_fold,
        )
        if validated.scheduled_at_utc <= now:
            raise AppError("PUBLISH_SCHEDULE_IN_PAST", "예약 게시 시각은 미래여야 합니다.", 422)
        if validated.scheduled_at_utc > now + timedelta(days=policy.max_schedule_days):
            raise AppError("PUBLISH_SCHEDULE_TOO_FAR", "게시 정책의 최대 예약 기간을 초과했습니다.", 422)
        return {
            "scheduled_datetime": validated.scheduled_at_utc,
            "local_datetime": validated.scheduled_local,
            "scheduled_at_utc": validated.scheduled_at_utc.isoformat(),
            "scheduled_local": validated.scheduled_local.isoformat(),
            "dst_fold": validated.fold,
            "local_day": validated.local_day,
        }
    local = now.astimezone(ZoneInfo(connection.site_timezone))
    return {
        "scheduled_datetime": now,
        "local_datetime": local.replace(tzinfo=None),
        "scheduled_at_utc": now.isoformat(),
        "scheduled_local": local.replace(tzinfo=None).isoformat(),
        "dst_fold": local.fold,
        "local_day": local.date(),
    }


def _validate_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise AppError("PUBLISH_TIMEZONE_INVALID", "IANA 사이트 시간대가 필요합니다.", 422) from exc


def _validate_options(
    options: dict[str, Any], connection: PublishingConnection
) -> None:
    canonical = options.get("canonical_url")
    if canonical:
        validate_site_url(str(canonical))
    allowed_meta = set(options.get("allowed_meta", {}))
    configured_allowlist = set(
        connection.safe_config_json.get("rest_meta_allowlist", [])
        if isinstance(connection.safe_config_json.get("rest_meta_allowlist", []), list)
        else []
    )
    if allowed_meta.difference(configured_allowlist):
        raise AppError(
            "PUBLISH_META_NOT_ALLOWLISTED",
            "등록된 공식 REST Meta 허용 목록의 필드만 게시할 수 있습니다.",
            422,
            fields=[
                {"path": "options.allowed_meta", "reason": key}
                for key in sorted(allowed_meta.difference(configured_allowlist))
            ],
        )
    if allowed_meta and connection.provider not in {
        PublishingProvider.WORDPRESS.value,
        PublishingProvider.CUSTOMER_CMS.value,
    }:
        raise AppError(
            "PUBLISH_META_UNSUPPORTED",
            "이 공식 provider 계약은 custom REST meta를 지원하지 않습니다.",
            422,
        )
    if options.get("create_missing_taxonomy") and "taxonomy_create" not in set(
        connection.capabilities
    ):
        raise AppError(
            "PUBLISH_TAXONOMY_CREATE_PERMISSION_MISSING",
            "연결 진단에서 taxonomy 생성 권한을 확인하지 못했습니다.",
            403,
        )
    option_capabilities = {
        "remote_author_id": "authors",
        "newsletter_id": "newsletter",
        "member_visibility": "visibility",
        "featured_media_placement": "media",
    }
    for option, capability in option_capabilities.items():
        if options.get(option) and capability not in set(connection.capabilities):
            raise AppError(
                "PUBLISH_OPTION_CAPABILITY_MISSING",
                "연결 진단에서 요청 옵션의 공식 API 기능을 확인하지 못했습니다.",
                403,
                fields=[{"path": f"options.{option}", "reason": capability}],
            )
    taxonomy_capability = {
        PublishingProvider.WORDPRESS.value: "tags",
        PublishingProvider.GHOST.value: "tags",
        PublishingProvider.BLOGGER.value: "labels",
    }.get(connection.provider)
    if (
        options.get("tags")
        and taxonomy_capability
        and taxonomy_capability not in set(connection.capabilities)
    ):
        raise AppError(
            "PUBLISH_TAXONOMY_CAPABILITY_MISSING",
            "연결 진단에서 태그 또는 Label 기능을 확인하지 못했습니다.",
            403,
        )
    if (
        options.get("category_names") or options.get("category_ids")
    ) and "categories" not in set(connection.capabilities):
        raise AppError(
            "PUBLISH_CATEGORY_CAPABILITY_MISSING",
            "연결 진단에서 Category 기능을 확인하지 못했습니다.",
            403,
        )
    if connection.provider == PublishingProvider.BLOGGER.value:
        labels = list(options.get("tags") or [])
        if len(labels) > 20 or sum(len(item) for item in labels) > 200:
            raise AppError(
                "BLOGGER_LABEL_LIMIT_EXCEEDED",
                "Blogger Label은 게시물당 20개, 전체 200자 이내여야 합니다.",
                422,
            )
    if connection.provider == PublishingProvider.GHOST.value and options.get("tags"):
        synced_tags = connection.site_settings_snapshot.get("tags")
        if not isinstance(synced_tags, list):
            raise AppError(
                "PUBLISH_SITE_SETTINGS_SYNC_REQUIRED",
                "Ghost Tag 매핑 전 사이트 설정 동기화가 필요합니다.",
                409,
            )
        known = {
            str(item.get("name", "")).casefold()
            for item in synced_tags
            if isinstance(item, dict)
        }
        missing = [
            str(item)
            for item in options["tags"]
            if str(item).casefold() not in known
        ]
        if missing and not options.get("create_missing_taxonomy"):
            raise AppError(
                "GHOST_TAG_NOT_FOUND",
                "Ghost에 없는 Tag가 있으며 자동 생성 정책이 비활성화되어 있습니다.",
                409,
                fields=[
                    {"path": "options.tags", "reason": item} for item in missing
                ],
            )
    if connection.provider == PublishingProvider.GHOST.value:
        for option, collection in (
            ("remote_author_id", "authors"),
            ("newsletter_id", "newsletters"),
        ):
            requested = options.get(option)
            if not requested:
                continue
            synced = connection.site_settings_snapshot.get(collection)
            if not isinstance(synced, list):
                raise AppError(
                    "PUBLISH_SITE_SETTINGS_SYNC_REQUIRED",
                    "Ghost 원격 ID 매핑 전 사이트 설정 동기화가 필요합니다.",
                    409,
                    fields=[{"path": f"options.{option}", "reason": collection}],
                )
            known_ids = {
                str(item.get("id"))
                for item in synced
                if isinstance(item, dict) and item.get("id")
            }
            if str(requested) not in known_ids:
                raise AppError(
                    "GHOST_REMOTE_MAPPING_NOT_FOUND",
                    "동기화된 Ghost 원격 항목에서 요청 ID를 찾을 수 없습니다.",
                    422,
                    fields=[{"path": f"options.{option}", "reason": str(requested)}],
                )


def _media_manifest(ready: PublishReadyContent) -> list[dict[str, Any]]:
    return [
        {
            "placement_key": item.placement_key,
            "asset_id": str(item.asset_id),
            "media_version_id": str(item.media_version_id),
            "object_ref": item.object_ref,
            "content_hash": item.content_hash,
            "mime_type": item.mime_type,
            "filename": item.filename,
            "alt_text": item.alt_text,
            "caption": item.caption,
            "rights_snapshot_hash": item.rights_snapshot_hash,
        }
        for item in ready.media
    ]


def _validate_featured_media(
    options: dict[str, Any], ready: PublishReadyContent
) -> None:
    placement = options.get("featured_media_placement")
    if placement and placement not in {item.placement_key for item in ready.media}:
        raise AppError(
            "PUBLISH_FEATURED_MEDIA_NOT_FOUND",
            "대표 이미지 placement가 승인된 현재 콘텐츠 미디어에 없습니다.",
            422,
        )


def _naver_package_hash(package: NaverPublishPackage) -> str:
    return canonical_hash(
        {
            "content_version_id": str(package.content_version_id),
            "content_hash": package.content_hash,
            "approval_snapshot_hash": package.approval_snapshot_hash,
            "title": package.title,
            "blocks": package.formatted_blocks,
            "images": package.image_manifest,
            "tags": package.tags,
            "checklist": package.checklist,
            "diff": package.diff_manifest,
            "unsupported": package.unsupported_blocks,
            "policy_version": package.policy_version,
            "policy_notice_hash": package.policy_notice_hash,
            "app_launch_url": package.app_launch_url,
        }
    )


def _assert_naver_package_integrity(package: NaverPublishPackage) -> None:
    notice_hash = canonical_hash(
        {"version": package.policy_version, "notice": package.policy_notice}
    )
    if (
        notice_hash != package.policy_notice_hash
        or _naver_package_hash(package) != package.package_hash
    ):
        raise AppError(
            "NAVER_PACKAGE_INTEGRITY_FAILED",
            "네이버 수동 게시 패키지의 무결성 검증에 실패했습니다.",
            409,
        )


def _public_media_manifest(ready: PublishReadyContent) -> list[dict[str, Any]]:
    return [
        {
            "placement_key": item.placement_key,
            "media_version_id": str(item.media_version_id),
            "content_hash": item.content_hash,
            "mime_type": item.mime_type,
            "filename": item.filename,
            "alt_text": item.alt_text,
            "caption": item.caption,
            "rights_snapshot_hash": item.rights_snapshot_hash,
        }
        for item in ready.media
    ]


def _unsupported_preview_options(
    provider: str, options: dict[str, Any]
) -> list[str]:
    present = {
        key for key, value in options.items() if value not in (None, [], {}, False)
    }
    supported = {
        PublishingProvider.WORDPRESS.value: {
            "excerpt",
            "category_ids",
            "category_names",
            "tags",
            "create_missing_taxonomy",
            "remote_author_id",
            "featured_media_placement",
            "comment_status",
            "tracking",
            "allowed_meta",
            "unsupported_block_policy",
        },
        PublishingProvider.GHOST.value: {
            "tags",
            "create_missing_taxonomy",
            "remote_author_id",
            "featured_media_placement",
            "canonical_url",
            "newsletter_id",
            "send_newsletter",
            "member_visibility",
            "tracking",
            "unsupported_block_policy",
        },
        PublishingProvider.BLOGGER.value: {
            "tags",
            "tracking",
            "unsupported_block_policy",
        },
    }.get(provider, present)
    return sorted(present.difference(supported))


def _assert_connection_active(connection: PublishingConnection) -> None:
    if connection.state != ConnectionState.ACTIVE.value:
        raise AppError(
            "PUBLISH_CONNECTION_NOT_ACTIVE",
            "진단을 통과한 ACTIVE 게시 연결만 사용할 수 있습니다.",
            409,
            fields=[{"path": "state", "reason": connection.state}],
        )


def _assert_publish_capability(
    connection: PublishingConnection, visibility: PublishVisibility
) -> None:
    if (
        connection.provider == PublishingProvider.BLOGGER.value
        and visibility
        not in {
            PublishVisibility.DRAFT,
            PublishVisibility.PUBLISH,
            PublishVisibility.SCHEDULED,
        }
    ):
        raise AppError(
            "BLOGGER_VISIBILITY_UNSUPPORTED",
            "Blogger 공식 API는 이 공개 범위 상태를 지원하지 않습니다.",
            422,
        )
    if (
        connection.provider == PublishingProvider.GHOST.value
        and visibility is PublishVisibility.PRIVATE
    ):
        raise AppError(
            "GHOST_PRIVATE_VISIBILITY_UNSUPPORTED",
            "Ghost의 Member Visibility는 작성자 전용 비공개 게시 상태와 같지 않습니다.",
            422,
        )
    required = {
        PublishVisibility.DRAFT: "draft",
        PublishVisibility.PUBLISH: "publish",
        PublishVisibility.SCHEDULED: "future",
        PublishVisibility.PENDING_REVIEW: "draft",
        PublishVisibility.PRIVATE: "publish",
    }[visibility]
    if required not in set(connection.capabilities):
        raise AppError(
            "PUBLISH_CAPABILITY_MISSING",
            "연결 진단에서 요청한 게시 기능 권한을 확인하지 못했습니다.",
            403,
            fields=[{"path": "capability", "reason": required}],
        )


def _assert_remote_expectations(
    post: PublishedPost,
    *,
    expected_etag: str | None,
    expected_hash: str,
    expected_updated_at: datetime | None,
) -> None:
    if expected_hash != post.remote_hash:
        raise AppError("REMOTE_SNAPSHOT_CONFLICT", "저장된 원격 해시가 변경되었습니다.", 409)
    if expected_etag is not None and expected_etag != post.remote_etag:
        raise AppError("REMOTE_ETAG_CONFLICT", "저장된 ETag가 변경되었습니다.", 409)
    if expected_updated_at is not None and expected_updated_at != post.remote_updated_at:
        raise AppError("REMOTE_UPDATED_AT_CONFLICT", "저장된 원격 수정 시각이 변경되었습니다.", 409)


def _assert_expected_version(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise AppError(
            "VERSION_CONFLICT",
            "기대 버전과 현재 버전이 일치하지 않습니다.",
            409,
            fields=[{"path": resource, "reason": f"expected={expected},actual={actual}"}],
        )


def _assert_lock(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise AppError(
            "OPTIMISTIC_LOCK_CONFLICT",
            "다른 요청이 먼저 리소스를 변경했습니다.",
            409,
            fields=[{"path": resource, "reason": f"expected={expected},actual={actual}"}],
        )


def _assert_same_request(actual: str, expected: str) -> None:
    if actual != expected:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "같은 workspace·actor·operation의 Idempotency-Key가 다른 요청에 사용되었습니다.",
            409,
        )
