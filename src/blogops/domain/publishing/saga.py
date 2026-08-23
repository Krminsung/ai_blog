"""Worker-only publish Saga execution with retry, conflict and compensation records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.publishing.enums import (
    ConflictAction,
    ConnectionOperation,
    ConnectionState,
    PublishOperation,
    PublishedPostState,
    PublishingProvider,
    PublishVisibility,
    RetryClass,
    SagaStepKind,
)
from blogops.domain.publishing.models import (
    PublicationPolicy,
    PublishedMediaBinding,
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
from blogops.domain.publishing.providers import (
    ConnectionContext,
    CMSProvider,
    MediaBinary,
    MediaBinaryResolver,
    ProviderCall,
    ProviderFailure,
    ProviderRegistry,
    PublishDocument,
    RemotePost,
    SecretResolver,
    UploadedMedia,
)
from blogops.domain.publishing.references import PublishingReadinessResolver, PublishReadyContent
from blogops.domain.publishing.rendering import render_for_cms
from blogops.domain.publishing.repository import PublishingRepository
from blogops.domain.publishing.rules import canonical_hash, redact_metadata
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


async def process_publish_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    readiness: PublishingReadinessResolver,
    providers: ProviderRegistry,
    secrets: SecretResolver,
    media_resolver: MediaBinaryResolver,
) -> tuple[str, int | None]:
    repo = PublishingRepository(session)
    job = await repo.publish_job(workspace_id, job_id, for_update=True)
    if job.state in {
        JobState.PARTIAL.value,
        JobState.FINAL_FAILED.value,
        JobState.CANCELLED.value,
    }:
        return job.state, job.retry_after_seconds
    if job.state == JobState.SUCCEEDED.value and job.cancel_requested_at is None:
        return job.state, job.retry_after_seconds
    principal = _worker_principal(job)
    if (
        job.cancel_requested_at is not None
        and job.state == JobState.CANCEL_REQUESTED.value
        and job.started_at is None
    ):
        job.attempt += 1
        return await _cancel_local_job(session, repo, principal, job)
    connection = await repo.connection(workspace_id, job.connection_id, for_update=True)
    policy = await repo.policy(workspace_id, job.policy_id)
    if (
        canonical_hash(policy.snapshot_json) != policy.snapshot_hash
        or policy.snapshot_hash != job.policy_snapshot_hash
        or job.policy_snapshot != policy.snapshot_json
        or canonical_hash(job.input_snapshot) != job.input_snapshot_hash
    ):
        state = await _final_failure(
            session,
            job,
            "PUBLISH_JOB_SNAPSHOT_MISMATCH",
            "게시 정책 또는 입력 스냅샷 무결성 검증에 실패했습니다.",
        )
        await _notify_and_emit(
            session,
            principal,
            job,
            action="publishing.job.final_failed",
            details={"error_code": job.error_code, "retry_class": RetryClass.FINAL.value},
        )
        await repo.flush("publish_job")
        return state
    try:
        provider = providers.require(
            PublishingProvider(connection.provider), connection.official_contract
        )
        secret = await secrets.resolve(connection.credential_secret_ref)
    except AppError as exc:
        state = await _final_failure(session, job, exc.code, exc.message)
        await _notify_and_emit(
            session,
            principal,
            job,
            action="publishing.job.final_failed",
            details={"error_code": exc.code, "retry_class": RetryClass.FINAL.value},
        )
        await repo.flush("publish_job")
        return state
    context = _connection_context(connection)
    if job.cancel_requested_at is not None and job.state in {
        JobState.CANCEL_REQUESTED.value,
        JobState.RETRYABLE_FAILED.value,
    }:
        job.attempt += 1
        try:
            return await _cancel_job(
                session, repo, principal, job, connection, provider, context, secret
            )
        except ProviderFailure as exc:
            await _record_provider_failure(session, job, exc)
            if (
                exc.retry_class
                in {RetryClass.NETWORK, RetryClass.RATE_LIMIT, RetryClass.SERVER}
                and job.attempt < job.max_attempts
            ):
                job.state = JobState.RETRYABLE_FAILED.value
                job.retry_after_seconds = exc.retry_after_seconds or min(
                    3_600, 5 * (2 ** max(job.attempt - 1, 0))
                )
            else:
                job.state = JobState.FINAL_FAILED.value
                job.finished_at = datetime.now(UTC)
                await _skip_pending_steps(session, job)
            job.error_code = exc.code
            job.error_detail = exc.detail
            await _notify_and_emit(
                session,
                principal,
                job,
                action=(
                    "publishing.job.cancel_retryable_failed"
                    if job.state == JobState.RETRYABLE_FAILED.value
                    else "publishing.job.cancel_final_failed"
                ),
                details={
                    "error_code": exc.code,
                    "retry_class": exc.retry_class.value,
                    "retry_after_seconds": job.retry_after_seconds,
                },
            )
            await repo.flush("publish_job_cancellation")
            return job.state, job.retry_after_seconds
    job.state = JobState.PUBLISHING.value
    job.started_at = job.started_at or datetime.now(UTC)
    job.attempt += 1
    job.error_code = None
    job.error_detail = None
    job.retry_after_seconds = None
    try:
        operation = PublishOperation(job.operation)
        if operation is PublishOperation.CREATE:
            partial = await _create_remote(
                session,
                repo,
                principal,
                job,
                connection,
                policy,
                provider,
                context,
                secret,
                readiness,
                media_resolver,
            )
        elif operation is PublishOperation.UPDATE:
            partial = await _update_remote(
                session,
                repo,
                principal,
                job,
                connection,
                policy,
                provider,
                context,
                secret,
                readiness,
                media_resolver,
            )
        elif operation is PublishOperation.DELETE:
            partial = await _delete_remote(
                session, repo, principal, job, provider, context, secret
            )
        elif operation is PublishOperation.RECONCILE:
            partial = await _reconcile_remote(
                session,
                repo,
                principal,
                job,
                connection,
                policy,
                provider,
                context,
                secret,
                readiness,
                media_resolver,
            )
        else:
            partial = await _rollback_remote(
                session, repo, principal, job, provider, context, secret
            )
        await _complete_job(session, principal, job, partial=partial)
    except ProviderFailure as exc:
        await _record_provider_failure(session, job, exc)
        retryable = exc.retry_class in {
            RetryClass.NETWORK,
            RetryClass.RATE_LIMIT,
            RetryClass.SERVER,
        }
        if retryable and job.attempt < job.max_attempts:
            job.state = JobState.RETRYABLE_FAILED.value
            job.retry_after_seconds = exc.retry_after_seconds or min(
                3_600, 5 * (2 ** max(job.attempt - 1, 0))
            )
        else:
            job.state = JobState.FINAL_FAILED.value
            job.finished_at = datetime.now(UTC)
            await _skip_pending_steps(session, job)
            if job.operation in {
                PublishOperation.CREATE.value,
                PublishOperation.UPDATE.value,
            } and job.quota_completed_at is None:
                await _release_quota(session, job)
        job.error_code = exc.code
        job.error_detail = exc.detail
        await _notify_and_emit(
            session,
            principal,
            job,
            action=(
                "publishing.job.retryable_failed"
                if job.state == JobState.RETRYABLE_FAILED.value
                else "publishing.job.final_failed"
            ),
            details={
                "error_code": exc.code,
                "retry_class": exc.retry_class.value,
                "retry_after_seconds": job.retry_after_seconds,
            },
        )
    except AppError as exc:
        await _final_failure(session, job, exc.code, exc.message)
        await _notify_and_emit(
            session,
            principal,
            job,
            action="publishing.job.final_failed",
            details={"error_code": exc.code, "retry_class": RetryClass.FINAL.value},
        )
    await repo.flush("publish_job")
    return job.state, job.retry_after_seconds


async def process_connection_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    providers: ProviderRegistry,
    secrets: SecretResolver,
) -> tuple[str, int | None]:
    repo = PublishingRepository(session)
    job = await repo.connection_job(workspace_id, job_id, for_update=True)
    if job.state in {JobState.SUCCEEDED.value, JobState.FINAL_FAILED.value}:
        return job.state, job.retry_after_seconds
    connection = await repo.connection(workspace_id, job.connection_id, for_update=True)
    previous_connection_state = connection.state
    job.state = JobState.VALIDATING.value
    job.started_at = job.started_at or datetime.now(UTC)
    job.attempt += 1
    job.error_code = None
    job.error_detail = None
    job.retry_after_seconds = None
    principal = Principal(
        subject_id=job.requested_by,
        workspace_id=workspace_id,
        session_id=None,
        permissions=frozenset(),
        authentication_method="worker",
    )
    try:
        operation = ConnectionOperation(job.operation)
        if operation is ConnectionOperation.DISCONNECT:
            await secrets.revoke(connection.credential_secret_ref)
            connection.state = ConnectionState.DISCONNECTED.value
            connection.disconnected_at = datetime.now(UTC)
            connection.last_error_code = None
            job.checks_json = [
                {"key": "credential_revocation", "ok": True},
                {"key": "new_publish_disabled", "ok": True},
            ]
            job.safe_result_json = {"existing_post_links_preserved": True}
        else:
            provider = providers.require(
                PublishingProvider(connection.provider), connection.official_contract
            )
            secret = (
                await secrets.refresh(connection.credential_secret_ref)
                if operation is ConnectionOperation.REFRESH
                else await secrets.resolve(connection.credential_secret_ref)
            )
            credential_expires_at = secret.expires_at()
            connection.credential_expires_at = credential_expires_at
            if (
                credential_expires_at is not None
                and credential_expires_at <= datetime.now(UTC)
            ):
                raise ProviderFailure(
                    code="PUBLISH_CREDENTIAL_EXPIRED",
                    detail="게시 Credential이 만료되어 갱신 또는 재인증이 필요합니다.",
                    retry_class=RetryClass.FINAL,
                )
            context = _connection_context(connection)
            if operation is ConnectionOperation.DIAGNOSE:
                call = await provider.diagnose(context, secret)
            elif operation is ConnectionOperation.REFRESH:
                call = await provider.refresh(context, secret)
            else:
                call = await provider.sync_settings(context, secret)
            diagnostic = call.value
            previous_settings_hash = connection.site_settings_hash
            job.checks_json = redact_metadata(diagnostic.checks)
            credential_expired = bool(
                credential_expires_at is not None
                and credential_expires_at <= datetime.now(UTC)
            )
            if credential_expires_at is not None:
                job.checks_json.append(
                    {
                        "key": "credential_lifecycle",
                        "ok": not credential_expired,
                        "expires_at": credential_expires_at.isoformat(),
                    }
                )
            api_expired = bool(
                connection.api_deprecation_at is not None
                and connection.api_deprecation_at <= datetime.now(UTC)
            )
            if connection.api_deprecation_at is not None:
                job.checks_json.append(
                    {
                        "key": "api_version_lifecycle",
                        "ok": not api_expired,
                        "api_version": connection.api_version,
                        "deprecation_at": connection.api_deprecation_at.isoformat(),
                    }
                )
            critical_keys = {
                "authentication",
                "oauth_scope",
                "api",
                "api_index",
                "blog",
                "timezone",
            }
            critical_failed = any(
                item.get("key") in critical_keys and not bool(item.get("ok"))
                for item in job.checks_json
                if isinstance(item, dict)
            )
            job.safe_result_json = {
                "capabilities": diagnostic.capabilities,
                "site_settings_hash": canonical_hash(diagnostic.site_settings),
                "previous_site_settings_hash": previous_settings_hash,
                "settings_changed": (
                    previous_settings_hash
                    != canonical_hash(diagnostic.site_settings)
                ),
                "provider_status": call.status_code,
            }
            connection.capabilities = diagnostic.capabilities
            connection.site_settings_snapshot = redact_metadata(diagnostic.site_settings)
            connection.site_settings_hash = canonical_hash(connection.site_settings_snapshot)
            connection.last_diagnosed_at = datetime.now(UTC)
            connection.last_success_at = datetime.now(UTC)
            connection.credential_expires_at = credential_expires_at
            if credential_expires_at is None:
                connection.credential_expiry_notified_for = None
            elif (
                credential_expires_at <= datetime.now(UTC) + timedelta(days=7)
                and connection.credential_expiry_notified_for
                != credential_expires_at
            ):
                session.add(
                    PublishingNotification(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        recipient_id=connection.created_by,
                        publish_job_id=None,
                        naver_package_id=None,
                        notification_type="PUBLISHING_CREDENTIAL_EXPIRING",
                        payload_json={
                            "connection_id": str(connection.id),
                            "expires_at": credential_expires_at.isoformat(),
                        },
                        due_at=datetime.now(UTC),
                    )
                )
                connection.credential_expiry_notified_for = credential_expires_at
            connection.last_error_code = (
                "PUBLISH_CREDENTIAL_EXPIRED"
                if credential_expired
                else "PUBLISH_DIAGNOSTIC_CRITICAL_CHECK_FAILED"
                if critical_failed
                else None
            )
            connection.state = (
                ConnectionState.EXPIRED.value
                if api_expired or credential_expired
                else ConnectionState.DEGRADED.value
                if critical_failed
                else ConnectionState.ACTIVE.value
            )
        job.state = JobState.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        await _audit_outbox(
            session,
            principal,
            action="publishing.connection.job_succeeded",
            target_type="publishing_connection_job",
            target_id=job.id,
            details={"connection_id": str(connection.id), "operation": job.operation},
        )
    except ProviderFailure as exc:
        retryable = exc.retry_class in {
            RetryClass.NETWORK,
            RetryClass.RATE_LIMIT,
            RetryClass.SERVER,
        }
        job.error_code = exc.code
        job.error_detail = exc.detail
        connection.last_error_code = exc.code
        connection.state = (
            ConnectionState.EXPIRED.value
            if exc.code == "PUBLISH_CREDENTIAL_EXPIRED"
            else ConnectionState.DEGRADED.value
        )
        if retryable and job.attempt < job.max_attempts:
            job.state = JobState.RETRYABLE_FAILED.value
            job.retry_after_seconds = exc.retry_after_seconds or min(3_600, 5 * 2 ** job.attempt)
        else:
            job.state = JobState.FINAL_FAILED.value
            job.finished_at = datetime.now(UTC)
        await _audit_outbox(
            session,
            principal,
            action=(
                "publishing.connection.job_retryable_failed"
                if job.state == JobState.RETRYABLE_FAILED.value
                else "publishing.connection.job_final_failed"
            ),
            target_type="publishing_connection_job",
            target_id=job.id,
            details={
                "connection_id": str(connection.id),
                "operation": job.operation,
                "error_code": exc.code,
                "retry_class": exc.retry_class.value,
            },
        )
    except AppError as exc:
        job.state = JobState.FINAL_FAILED.value
        job.error_code = exc.code
        job.error_detail = exc.message
        job.finished_at = datetime.now(UTC)
        connection.last_error_code = exc.code
        connection.state = ConnectionState.DEGRADED.value
        await _audit_outbox(
            session,
            principal,
            action="publishing.connection.job_final_failed",
            target_type="publishing_connection_job",
            target_id=job.id,
            details={
                "connection_id": str(connection.id),
                "operation": job.operation,
                "error_code": exc.code,
                "retry_class": RetryClass.FINAL.value,
            },
        )
    if connection.state != previous_connection_state:
        session.add(
            PublishingNotification(
                id=uuid4(),
                workspace_id=workspace_id,
                recipient_id=connection.created_by,
                publish_job_id=None,
                naver_package_id=None,
                notification_type="PUBLISHING_CONNECTION_STATE_CHANGED",
                payload_json={
                    "connection_id": str(connection.id),
                    "previous_state": previous_connection_state,
                    "state": connection.state,
                    "error_code": connection.last_error_code,
                },
                due_at=datetime.now(UTC),
            )
        )
    await repo.flush("publishing_connection_job")
    return job.state, job.retry_after_seconds


async def _create_remote(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    connection: PublishingConnection,
    policy: PublicationPolicy,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    readiness: PublishingReadinessResolver,
    media_resolver: MediaBinaryResolver,
) -> bool:
    ready = await _resolve_ready(job, connection, policy, readiness)
    await _succeed_local_step(session, job, SagaStepKind.VALIDATE_READINESS, {"approval_snapshot_hash": ready.approval_snapshot_hash})
    media_uploads, media_failures = await _upload_media(
        session, job, provider, context, secret, ready, media_resolver
    )
    document = _document(job, ready, media_uploads)
    step = await _running_step(session, job, SagaStepKind.WRITE_POST)
    found_call = await provider.find_by_marker(context, secret, job.idempotency_marker)
    await _record_call(session, job, step, found_call)
    if found_call.value is None:
        call = await provider.create_post(context, secret, document)
    else:
        _assert_remote_integrity(found_call.value)
        call = await provider.update_post(
            context, secret, found_call.value, document
        )
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    remote = call.value
    _assert_remote_integrity(remote)
    post = await session.scalar(
        select(PublishedPost).where(
            PublishedPost.workspace_id == job.workspace_id,
            PublishedPost.created_by_job_id == job.id,
        )
    )
    if post is None:
        post = PublishedPost(
            id=uuid4(),
            workspace_id=job.workspace_id,
            content_id=job.content_id,
            content_version_id=job.content_version_id,
            content_hash=job.content_hash,
            approval_request_id=job.approval_request_id,
            connection_id=job.connection_id,
            created_by_job_id=job.id,
            naver_package_id=None,
            provider=connection.provider,
            remote_site_id=connection.remote_site_id,
            remote_id=remote.remote_id,
            remote_url=remote.remote_url,
            state=_post_state(remote.state, bool(media_failures)),
            remote_etag=remote.etag,
            remote_hash=remote.remote_hash,
            remote_updated_at=remote.updated_at,
            local_snapshot_hash=job.input_snapshot_hash,
            last_reconciled_at=datetime.now(UTC),
            conflict_json=None,
            lock_version=1,
        )
        session.add(post)
    else:
        _apply_remote(post, remote, partial=bool(media_failures))
    job.target_published_post_id = post.id
    verified = await _verify_remote(session, job, provider, context, secret, remote.remote_id)
    _apply_remote(post, verified, partial=bool(media_failures))
    await _snapshot_remote(session, job, post, verified, "AFTER_CREATE")
    job.result_json = {
        "published_post_id": str(post.id),
        "remote_id": post.remote_id,
        "remote_url": post.remote_url,
        "media_failures": media_failures,
        "media_remediation": _media_remediation(media_failures),
        "unsupported_options": _unsupported_options(connection, document.options),
    }
    await _complete_quota(session, job)
    return bool(media_failures)


async def _update_remote(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    connection: PublishingConnection,
    policy: PublicationPolicy,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    readiness: PublishingReadinessResolver,
    media_resolver: MediaBinaryResolver,
) -> bool:
    ready = await _resolve_ready(job, connection, policy, readiness)
    await _succeed_local_step(session, job, SagaStepKind.VALIDATE_READINESS, {"approval_snapshot_hash": ready.approval_snapshot_hash})
    post = await _target_post(repo, job)
    remote = await _fetch_remote(session, job, provider, context, secret, post)
    action = ConflictAction(job.input_snapshot.get("conflict_action", ConflictAction.ABORT.value))
    conflict = _remote_conflict(job, remote)
    if conflict and action is ConflictAction.ABORT:
        raise ProviderFailure(code="PUBLISH_REMOTE_CONFLICT", detail="원격 게시물이 마지막 동기화 후 수정되었습니다.", retry_class=RetryClass.FINAL, status_code=409)
    await _snapshot_remote(session, job, post, remote, "BEFORE_UPDATE")
    if conflict and action is ConflictAction.IMPORT_REMOTE:
        _apply_remote(post, remote, partial=False)
        post.state = PublishedPostState.CONFLICT.value
        post.conflict_json = {"resolution": "IMPORT_REMOTE_SNAPSHOT", "snapshot_hash": remote.remote_hash}
        job.result_json = {"published_post_id": str(post.id), "remote_imported": True}
        return False
    media_uploads, media_failures = await _upload_media(session, job, provider, context, secret, ready, media_resolver)
    document = _document(job, ready, media_uploads)
    step = await _running_step(session, job, SagaStepKind.WRITE_POST)
    call = await provider.update_post(context, secret, remote, document)
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    verified = await _verify_remote(session, job, provider, context, secret, post.remote_id)
    post.content_version_id = ready.content_version_id
    post.content_hash = ready.content_hash
    post.approval_request_id = ready.approval_request_id
    post.local_snapshot_hash = job.input_snapshot_hash
    post.conflict_json = None
    _apply_remote(post, verified, partial=bool(media_failures))
    await _snapshot_remote(session, job, post, verified, "AFTER_UPDATE")
    job.result_json = {
        "published_post_id": str(post.id),
        "remote_id": post.remote_id,
        "remote_url": post.remote_url,
        "media_failures": media_failures,
        "media_remediation": _media_remediation(media_failures),
        "unsupported_options": _unsupported_options(connection, document.options),
    }
    await _complete_quota(session, job)
    return bool(media_failures)


async def _delete_remote(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
) -> bool:
    del principal
    post = await _target_post(repo, job)
    try:
        remote = await _fetch_remote(session, job, provider, context, secret, post)
    except ProviderFailure as exc:
        if exc.status_code != 404:
            raise
        await _record_provider_failure(session, job, exc)
        fetch_step = await session.scalar(
            select(PublishSagaStep).where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.step_kind == SagaStepKind.FETCH_REMOTE.value,
            )
        )
        if fetch_step is not None:
            fetch_step.state = StepState.SUCCEEDED.value
            fetch_step.error_code = None
            fetch_step.response_metadata = {"remote_missing": True, "status": 404}
            fetch_step.finished_at = datetime.now(UTC)
        post.state = PublishedPostState.DELETED.value
        post.deleted_at = datetime.now(UTC)
        post.last_reconciled_at = datetime.now(UTC)
        job.result_json = {
            "published_post_id": str(post.id),
            "remote_id": post.remote_id,
            "deleted": True,
            "already_missing": True,
        }
        return False
    if _remote_conflict(job, remote):
        raise ProviderFailure(code="PUBLISH_REMOTE_CONFLICT", detail="원격 게시물이 마지막 동기화 후 수정되어 삭제를 중단했습니다.", retry_class=RetryClass.FINAL, status_code=409)
    await _snapshot_remote(session, job, post, remote, "BEFORE_DELETE")
    step = await _running_step(session, job, SagaStepKind.WRITE_POST)
    call = await provider.delete_post(context, secret, remote, force=bool(job.input_snapshot.get("force_delete")))
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    post.state = PublishedPostState.DELETED.value if job.input_snapshot.get("force_delete") else PublishedPostState.TRASHED.value
    post.deleted_at = datetime.now(UTC)
    post.last_reconciled_at = datetime.now(UTC)
    job.result_json = {"published_post_id": str(post.id), "remote_id": post.remote_id, "deleted": True, "force": bool(job.input_snapshot.get("force_delete"))}
    return False


async def _reconcile_remote(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    connection: PublishingConnection,
    policy: PublicationPolicy,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    readiness: PublishingReadinessResolver,
    media_resolver: MediaBinaryResolver,
) -> bool:
    del principal
    post = await _target_post(repo, job)
    try:
        remote = await _fetch_remote(
            session,
            job,
            provider,
            context,
            secret,
            post,
            step_kind=SagaStepKind.RECONCILE,
        )
    except ProviderFailure as exc:
        if exc.status_code != 404:
            raise
        await _record_provider_failure(session, job, exc)
        reconcile_step = await session.scalar(
            select(PublishSagaStep).where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.step_kind == SagaStepKind.RECONCILE.value,
            )
        )
        if reconcile_step is not None:
            reconcile_step.state = StepState.SUCCEEDED.value
            reconcile_step.error_code = None
            reconcile_step.response_metadata = {"remote_missing": True, "status": 404}
            reconcile_step.finished_at = datetime.now(UTC)
        post.state = PublishedPostState.REMOTE_MISSING.value
        post.conflict_json = {"remote_missing": True}
        post.last_reconciled_at = datetime.now(UTC)
        job.result_json = {"published_post_id": str(post.id), "remote_missing": True}
        return False
    if not _remote_conflict(job, remote):
        post.last_reconciled_at = datetime.now(UTC)
        job.result_json = {"published_post_id": str(post.id), "in_sync": True}
        return False
    if await _is_expected_scheduled_transition(session, post, remote):
        await _snapshot_remote(session, job, post, remote, "SCHEDULE_TRANSITION")
        _apply_remote(post, remote, partial=False)
        post.conflict_json = None
        job.result_json = {
            "published_post_id": str(post.id),
            "scheduled_transition": True,
            "remote_state": remote.state,
        }
        return False
    await _snapshot_remote(session, job, post, remote, "RECONCILIATION_CONFLICT")
    action = ConflictAction(job.input_snapshot.get("conflict_action", ConflictAction.ABORT.value))
    if action is ConflictAction.ABORT:
        post.state = PublishedPostState.CONFLICT.value
        post.conflict_json = {"remote_hash": remote.remote_hash, "remote_etag": remote.etag}
        raise ProviderFailure(code="PUBLISH_REMOTE_CONFLICT", detail="원격 게시물과 로컬 스냅샷이 충돌합니다.", retry_class=RetryClass.FINAL, status_code=409)
    if action is ConflictAction.IMPORT_REMOTE:
        _apply_remote(post, remote, partial=False)
        post.state = PublishedPostState.CONFLICT.value
        post.conflict_json = {"resolution": "IMPORT_REMOTE_SNAPSHOT", "remote_hash": remote.remote_hash}
        job.result_json = {"published_post_id": str(post.id), "remote_imported": True}
        return False
    ready = await _resolve_ready(job, connection, policy, readiness)
    media_uploads, media_failures = await _upload_media(
        session,
        job,
        provider,
        context,
        secret,
        ready,
        media_resolver,
    )
    document = _document(job, ready, media_uploads)
    step = await _running_step(session, job, SagaStepKind.WRITE_POST)
    call = await provider.update_post(context, secret, remote, document)
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    verified = await _verify_remote(
        session, job, provider, context, secret, post.remote_id
    )
    _apply_remote(post, verified, partial=bool(media_failures))
    await _snapshot_remote(session, job, post, verified, "AFTER_RECONCILE_OVERWRITE")
    post.conflict_json = None
    job.result_json = {
        "published_post_id": str(post.id),
        "remote_overwritten": True,
        "media_failures": media_failures,
        "media_remediation": _media_remediation(media_failures),
        "unsupported_options": _unsupported_options(connection, document.options),
    }
    return bool(media_failures)


async def _rollback_remote(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
) -> bool:
    del principal
    post = await _target_post(repo, job)
    remote = await _fetch_remote(session, job, provider, context, secret, post)
    if _remote_conflict(job, remote):
        raise ProviderFailure(code="PUBLISH_REMOTE_CONFLICT", detail="원격 게시물이 변경되어 rollback을 중단했습니다.", retry_class=RetryClass.FINAL, status_code=409)
    await _snapshot_remote(session, job, post, remote, "BEFORE_ROLLBACK")
    snapshot_id = UUID(str(job.input_snapshot["snapshot_id"]))
    snapshot = await repo.remote_snapshot(job.workspace_id, snapshot_id)
    if (
        snapshot.published_post_id != post.id
        or snapshot.snapshot_hash != job.input_snapshot.get("snapshot_hash")
        or canonical_hash(snapshot.snapshot_json) != snapshot.snapshot_hash
    ):
        raise ProviderFailure(code="ROLLBACK_SNAPSHOT_MISMATCH", detail="rollback 스냅샷 무결성 검증에 실패했습니다.", retry_class=RetryClass.FINAL)
    step = await _running_step(session, job, SagaStepKind.ROLLBACK)
    call = await provider.restore_snapshot(context, secret, remote, snapshot.snapshot_json)
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    verified = await _verify_remote(session, job, provider, context, secret, post.remote_id)
    _apply_remote(post, verified, partial=False)
    await _snapshot_remote(session, job, post, verified, "AFTER_ROLLBACK")
    post.conflict_json = None
    job.result_json = {"published_post_id": str(post.id), "restored_snapshot_id": str(snapshot.id)}
    return False


async def _resolve_ready(
    job: PublishJob,
    connection: PublishingConnection,
    policy: PublicationPolicy,
    readiness: PublishingReadinessResolver,
) -> PublishReadyContent:
    ready = await readiness.resolve(
        workspace_id=job.workspace_id,
        content_id=job.content_id,
        content_version_id=job.content_version_id,
        content_hash=job.content_hash,
        approval_request_id=job.approval_request_id,
        channel=connection.provider,
        require_media_license=policy.require_media_license,
    )
    if ready.approval_snapshot_hash != job.approval_snapshot_hash:
        raise AppError("PUBLISH_APPROVAL_SNAPSHOT_MISMATCH", "승인 스냅샷이 작업 생성 이후 변경되었습니다.", 409)
    return ready


async def _upload_media(
    session: AsyncSession,
    job: PublishJob,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    ready: PublishReadyContent,
    media_resolver: MediaBinaryResolver,
) -> tuple[dict[str, UploadedMedia], list[dict[str, str]]]:
    if not ready.media:
        await _succeed_local_step(session, job, SagaStepKind.UPLOAD_MEDIA, {"count": 0})
        return {}, []
    step = await _running_step(session, job, SagaStepKind.UPLOAD_MEDIA)
    uploads: dict[str, UploadedMedia] = {}
    failures: list[dict[str, str]] = []
    for item in ready.media:
        try:
            binding = await session.scalar(
                select(PublishedMediaBinding).where(
                    PublishedMediaBinding.workspace_id == job.workspace_id,
                    PublishedMediaBinding.connection_id == job.connection_id,
                    PublishedMediaBinding.media_version_id == item.media_version_id,
                    PublishedMediaBinding.media_content_hash == item.content_hash,
                )
            )
            if binding is not None:
                uploads[item.placement_key] = UploadedMedia(
                    remote_id=binding.remote_media_id,
                    remote_url=binding.remote_url,
                    placement_key=item.placement_key,
                )
                continue
            content = await media_resolver.resolve(
                item.object_ref, expected_hash=item.content_hash
            )
            call = await provider.upload_media(
                context,
                secret,
                MediaBinary(
                    placement_key=item.placement_key,
                    filename=item.filename,
                    mime_type=item.mime_type,
                    content=content,
                    alt_text=item.alt_text,
                    caption=item.caption,
                ),
            )
            await _record_call(session, job, step, call)
            uploads[item.placement_key] = call.value
            session.add(
                PublishedMediaBinding(
                    id=uuid4(),
                    workspace_id=job.workspace_id,
                    connection_id=job.connection_id,
                    media_version_id=item.media_version_id,
                    media_content_hash=item.content_hash,
                    remote_media_id=call.value.remote_id,
                    remote_url=call.value.remote_url,
                    uploaded_by_job_id=job.id,
                )
            )
        except ProviderFailure as exc:
            if exc.retry_class in {
                RetryClass.NETWORK,
                RetryClass.RATE_LIMIT,
                RetryClass.SERVER,
            }:
                raise
            await _record_provider_failure(session, job, exc, step=step)
            failures.append(
                {
                    "placement_key": item.placement_key,
                    "error_code": exc.code,
                    "retry_class": exc.retry_class.value,
                }
            )
    step.state = StepState.SUCCEEDED.value if not failures else StepState.FAILED.value
    step.response_metadata = {"uploaded": len(uploads), "failed": len(failures)}
    step.finished_at = datetime.now(UTC)
    return uploads, failures


def _document(
    job: PublishJob,
    ready: PublishReadyContent,
    media_uploads: dict[str, UploadedMedia],
) -> PublishDocument:
    options = dict(job.input_snapshot.get("options") or {})
    media_urls = {
        placement: item.remote_url for placement, item in media_uploads.items()
    }
    rendered = render_for_cms(
        ready.document,
        media_urls,
        tracking=dict(options.get("tracking") or {}),
        attributions=[
            item.attribution_text
            for item in ready.media
            if item.attribution_text
        ],
    )
    featured = options.get("featured_media_placement")
    if featured and featured in media_uploads:
        options["featured_media_remote_id"] = media_uploads[featured].remote_id
        options["featured_media_url"] = media_uploads[featured].remote_url
        source_media = next(
            (item for item in ready.media if item.placement_key == featured), None
        )
        if source_media is not None:
            options["featured_media_alt"] = source_media.alt_text
            options["featured_media_caption"] = source_media.caption
    if rendered.unsupported and options.get("unsupported_block_policy") == "REJECT":
        raise AppError("PUBLISH_UNSUPPORTED_BLOCK", "채널에서 지원하지 않는 콘텐츠 블록이 있습니다.", 409, fields=[{"path": "block", "reason": str(item.get("block_key"))} for item in rendered.unsupported])
    return PublishDocument(
        title=ready.title,
        html=rendered.html,
        plain_text=ready.plain_text,
        visibility=PublishVisibility(job.visibility),
        scheduled_at_utc=job.scheduled_at_utc if job.visibility == PublishVisibility.SCHEDULED.value else None,
        idempotency_marker=job.idempotency_marker,
        options=options,
        media_urls=media_urls,
    )


async def _fetch_remote(
    session: AsyncSession,
    job: PublishJob,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    post: PublishedPost,
    *,
    step_kind: SagaStepKind = SagaStepKind.FETCH_REMOTE,
) -> RemotePost:
    step = await _running_step(session, job, step_kind)
    call = await provider.get_post(context, secret, post.remote_id)
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    return call.value


async def _verify_remote(
    session: AsyncSession,
    job: PublishJob,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
    remote_id: str,
) -> RemotePost:
    step = await _running_step(session, job, SagaStepKind.VERIFY_REMOTE)
    call = await provider.get_post(context, secret, remote_id)
    await _record_call(session, job, step, call)
    await _finish_step(step, call)
    return call.value


async def _snapshot_remote(
    session: AsyncSession,
    job: PublishJob,
    post: PublishedPost,
    remote: RemotePost,
    reason: str,
) -> RemotePostSnapshot:
    _assert_remote_integrity(remote)
    existing = await session.scalar(
        select(RemotePostSnapshot).where(
            RemotePostSnapshot.workspace_id == job.workspace_id,
            RemotePostSnapshot.published_post_id == post.id,
            RemotePostSnapshot.snapshot_hash == remote.remote_hash,
        )
    )
    if existing is not None:
        await _succeed_local_step(session, job, SagaStepKind.SNAPSHOT_REMOTE, {"snapshot_id": str(existing.id), "reused": True})
        return existing
    snapshot = RemotePostSnapshot(
        id=uuid4(),
        workspace_id=job.workspace_id,
        published_post_id=post.id,
        captured_by_job_id=job.id,
        reason=reason,
        snapshot_json=remote.snapshot,
        snapshot_hash=remote.remote_hash,
        remote_etag=remote.etag,
        remote_updated_at=remote.updated_at,
    )
    session.add(snapshot)
    await _succeed_local_step(session, job, SagaStepKind.SNAPSHOT_REMOTE, {"snapshot_id": str(snapshot.id), "snapshot_hash": snapshot.snapshot_hash})
    return snapshot


async def _target_post(repo: PublishingRepository, job: PublishJob) -> PublishedPost:
    if job.target_published_post_id is None:
        raise ProviderFailure(code="PUBLISH_TARGET_POST_REQUIRED", detail="원격 작업 대상 게시물 연결이 없습니다.", retry_class=RetryClass.FINAL)
    post = await repo.published_post(
        job.workspace_id, job.target_published_post_id, for_update=True
    )
    expected_hash = job.input_snapshot.get("expected_remote_hash")
    expected_etag = job.input_snapshot.get("expected_remote_etag")
    expected_updated_at = job.input_snapshot.get("expected_remote_updated_at")
    if expected_hash is not None and post.remote_hash != expected_hash:
        raise ProviderFailure(
            code="PUBLISH_LOCAL_REMOTE_SNAPSHOT_STALE",
            detail="게시 작업 생성 후 로컬 원격 스냅샷이 변경되었습니다.",
            retry_class=RetryClass.FINAL,
            status_code=409,
        )
    if expected_etag is not None and post.remote_etag != expected_etag:
        raise ProviderFailure(
            code="PUBLISH_LOCAL_ETAG_STALE",
            detail="게시 작업 생성 후 저장된 ETag가 변경되었습니다.",
            retry_class=RetryClass.FINAL,
            status_code=409,
        )
    if (
        expected_updated_at is not None
        and (
            post.remote_updated_at is None
            or post.remote_updated_at.isoformat() != expected_updated_at
        )
    ):
        raise ProviderFailure(
            code="PUBLISH_LOCAL_UPDATED_AT_STALE",
            detail="게시 작업 생성 후 저장된 원격 수정 시각이 변경되었습니다.",
            retry_class=RetryClass.FINAL,
            status_code=409,
        )
    return post


async def _is_expected_scheduled_transition(
    session: AsyncSession,
    post: PublishedPost,
    remote: RemotePost,
) -> bool:
    if (
        post.state != PublishedPostState.SCHEDULED.value
        or _post_state(remote.state, False) != PublishedPostState.PUBLISHED.value
    ):
        return False
    previous = await session.scalar(
        select(RemotePostSnapshot)
        .where(
            RemotePostSnapshot.workspace_id == post.workspace_id,
            RemotePostSnapshot.published_post_id == post.id,
            RemotePostSnapshot.snapshot_hash == post.remote_hash,
        )
        .order_by(RemotePostSnapshot.captured_at.desc())
        .limit(1)
    )
    if previous is None:
        return False
    ignored = {
        "date_gmt",
        "modified_gmt",
        "published_at",
        "updated_at",
        "published",
        "updated",
        "status",
        "url",
    }
    prior_body = {
        key: value
        for key, value in previous.snapshot_json.items()
        if key not in ignored
    }
    remote_body = {
        key: value for key, value in remote.snapshot.items() if key not in ignored
    }
    return canonical_hash(prior_body) == canonical_hash(remote_body)


def _remote_conflict(job: PublishJob, remote: RemotePost) -> bool:
    _assert_remote_integrity(remote)
    expected_hash = str(job.input_snapshot.get("expected_remote_hash", ""))
    expected_etag = job.input_snapshot.get("expected_remote_etag")
    expected_updated_at = job.input_snapshot.get("expected_remote_updated_at")
    return bool(
        remote.remote_hash != expected_hash
        or (expected_etag is not None and remote.etag != expected_etag)
        or (
            expected_updated_at is not None
            and (
                remote.updated_at is None
                or remote.updated_at.isoformat() != expected_updated_at
            )
        )
    )


def _apply_remote(post: PublishedPost, remote: RemotePost, *, partial: bool) -> None:
    _assert_remote_integrity(remote)
    if post.remote_id and post.remote_id != remote.remote_id:
        raise ProviderFailure(
            code="PUBLISH_REMOTE_ID_CHANGED",
            detail="공식 CMS 응답의 원격 Post ID가 기존 연결과 일치하지 않습니다.",
            retry_class=RetryClass.FINAL,
        )
    post.remote_id = remote.remote_id
    post.remote_url = remote.remote_url
    post.remote_etag = remote.etag
    post.remote_hash = remote.remote_hash
    post.remote_updated_at = remote.updated_at
    post.last_reconciled_at = datetime.now(UTC)
    post.state = _post_state(remote.state, partial)


def _assert_remote_integrity(remote: RemotePost) -> None:
    if canonical_hash(remote.snapshot) != remote.remote_hash:
        raise ProviderFailure(
            code="PUBLISH_REMOTE_SNAPSHOT_HASH_MISMATCH",
            detail="공식 CMS adapter의 원격 스냅샷 해시가 본문과 일치하지 않습니다.",
            retry_class=RetryClass.FINAL,
        )


def _post_state(remote_state: str, partial: bool) -> str:
    if partial:
        return PublishedPostState.PARTIAL.value
    normalized = remote_state.casefold()
    if normalized in {"published", "publish", "live"}:
        return PublishedPostState.PUBLISHED.value
    if normalized in {"scheduled", "future"}:
        return PublishedPostState.SCHEDULED.value
    if normalized in {"deleted"}:
        return PublishedPostState.DELETED.value
    return PublishedPostState.DRAFT.value


def _unsupported_options(
    connection: PublishingConnection, options: dict[str, Any]
) -> list[str]:
    internal = {
        "featured_media_remote_id",
        "featured_media_url",
        "featured_media_alt",
        "featured_media_caption",
    }
    present = {
        key
        for key, value in options.items()
        if value not in (None, [], {}, False) and key not in internal
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
    }.get(connection.provider, present)
    # Slugs are intentionally held by the idempotency marker until a durable remote ID exists.
    return sorted(present.difference(supported))


def _media_remediation(failures: list[dict[str, str]]) -> str | None:
    if not failures:
        return None
    retryable = {RetryClass.NETWORK.value, RetryClass.RATE_LIMIT.value, RetryClass.SERVER.value}
    if any(item.get("retry_class") in retryable for item in failures):
        return "RETRY_JOB"
    return "EDIT_CONTENT_OR_UPLOAD_MEDIA_MANUALLY"


async def _running_step(
    session: AsyncSession, job: PublishJob, kind: SagaStepKind
) -> PublishSagaStep:
    step = await session.scalar(
        select(PublishSagaStep)
        .where(
            PublishSagaStep.workspace_id == job.workspace_id,
            PublishSagaStep.job_id == job.id,
            PublishSagaStep.step_kind == kind.value,
        )
        .with_for_update()
    )
    if step is None:
        if kind is not SagaStepKind.CANCEL_REMOTE:
            raise ProviderFailure(code="PUBLISH_SAGA_STEP_MISSING", detail="게시 Saga 단계가 없습니다.", retry_class=RetryClass.FINAL)
        sequence = int(
            await session.scalar(
                select(func.coalesce(func.max(PublishSagaStep.sequence), 0)).where(
                    PublishSagaStep.workspace_id == job.workspace_id,
                    PublishSagaStep.job_id == job.id,
                )
            )
            or 0
        ) + 1
        step = PublishSagaStep(
            id=uuid4(),
            workspace_id=job.workspace_id,
            job_id=job.id,
            sequence=sequence,
            step_kind=kind.value,
            state=StepState.PENDING.value,
            attempt=0,
            request_metadata={},
            response_metadata={},
            lock_version=1,
        )
        session.add(step)
    step.state = StepState.RUNNING.value
    step.attempt += 1
    step.started_at = datetime.now(UTC)
    step.error_code = None
    return step


async def _succeed_local_step(
    session: AsyncSession,
    job: PublishJob,
    kind: SagaStepKind,
    metadata: dict[str, Any],
) -> None:
    step = await _running_step(session, job, kind)
    step.state = StepState.SUCCEEDED.value
    step.response_metadata = redact_metadata(metadata)
    step.finished_at = datetime.now(UTC)


async def _finish_step(step: PublishSagaStep, call: ProviderCall[Any]) -> None:
    step.state = StepState.SUCCEEDED.value
    step.request_metadata = call.safe_request_metadata()
    step.response_metadata = call.safe_response_metadata()
    step.finished_at = datetime.now(UTC)


async def _record_call(
    session: AsyncSession,
    job: PublishJob,
    step: PublishSagaStep,
    call: ProviderCall[Any],
) -> None:
    session.add(
        PublishAttempt(
            id=uuid4(),
            workspace_id=job.workspace_id,
            job_id=job.id,
            step_id=step.id,
            attempt_number=await _next_attempt_number(session, job, step),
            provider_request_id=call.provider_request_id,
            method=call.method,
            endpoint_path=call.endpoint_path,
            request_metadata=call.safe_request_metadata(),
            response_status=call.status_code,
            response_metadata=call.safe_response_metadata(),
            retry_class=RetryClass.FINAL.value,
            error_code=None,
            remote_id=(
                call.value.remote_id if hasattr(call.value, "remote_id") else None
            ),
        )
    )


async def _record_provider_failure(
    session: AsyncSession,
    job: PublishJob,
    exc: ProviderFailure,
    *,
    step: PublishSagaStep | None = None,
) -> None:
    selected = step
    if selected is None:
        selected = await session.scalar(
            select(PublishSagaStep)
            .where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.state == StepState.RUNNING.value,
            )
            .order_by(PublishSagaStep.sequence.desc())
            .limit(1)
        )
    if selected is None:
        selected = await session.scalar(
            select(PublishSagaStep)
            .where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.state == StepState.PENDING.value,
            )
            .order_by(PublishSagaStep.sequence)
            .limit(1)
        )
    if selected is None:
        return
    selected.state = StepState.RETRYING.value if exc.retry_class is not RetryClass.FINAL else StepState.FAILED.value
    selected.error_code = exc.code
    selected.response_metadata = exc.response_metadata
    selected.finished_at = datetime.now(UTC)
    session.add(
        PublishAttempt(
            id=uuid4(),
            workspace_id=job.workspace_id,
            job_id=job.id,
            step_id=selected.id,
            attempt_number=await _next_attempt_number(session, job, selected),
            provider_request_id=None,
            method=exc.method,
            endpoint_path=exc.endpoint_path,
            request_metadata=exc.request_metadata,
            response_status=exc.status_code,
            response_metadata=exc.response_metadata,
            retry_class=exc.retry_class.value,
            error_code=exc.code,
            remote_id=None,
        )
    )


async def _next_attempt_number(
    session: AsyncSession, job: PublishJob, step: PublishSagaStep
) -> int:
    latest = await session.scalar(
        select(PublishAttempt.attempt_number)
        .where(
            PublishAttempt.workspace_id == job.workspace_id,
            PublishAttempt.job_id == job.id,
            PublishAttempt.step_id == step.id,
        )
        .order_by(PublishAttempt.attempt_number.desc())
        .limit(1)
    )
    pending = [
        item.attempt_number
        for item in session.new
        if isinstance(item, PublishAttempt)
        and item.workspace_id == job.workspace_id
        and item.job_id == job.id
        and item.step_id == step.id
    ]
    return max([int(latest or 0), *pending]) + 1


async def _complete_job(
    session: AsyncSession, principal: Principal, job: PublishJob, *, partial: bool
) -> None:
    job.state = JobState.PARTIAL.value if partial else JobState.SUCCEEDED.value
    job.finished_at = datetime.now(UTC)
    await _notify_and_emit(
        session,
        principal,
        job,
        action=("publishing.job.partial" if partial else "publishing.job.succeeded"),
        details={"result": redact_metadata(job.result_json or {})},
    )
    await _succeed_local_step(
        session,
        job,
        SagaStepKind.NOTIFY,
        {"notification_type": "PARTIAL" if partial else "SUCCEEDED"},
    )
    await _skip_pending_steps(session, job)


async def _skip_pending_steps(session: AsyncSession, job: PublishJob) -> None:
    pending = list(
        await session.scalars(
            select(PublishSagaStep).where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.state == StepState.PENDING.value,
            )
        )
    )
    for step in pending:
        if step.state == StepState.PENDING.value:
            step.state = StepState.SKIPPED.value
            step.finished_at = job.finished_at or datetime.now(UTC)


async def _record_local_failure(
    session: AsyncSession, job: PublishJob, error_code: str
) -> None:
    step = await session.scalar(
        select(PublishSagaStep)
        .where(
            PublishSagaStep.workspace_id == job.workspace_id,
            PublishSagaStep.job_id == job.id,
            PublishSagaStep.state.in_(
                {StepState.RUNNING.value, StepState.PENDING.value}
            ),
        )
        .order_by(
            (PublishSagaStep.state == StepState.RUNNING.value).desc(),
            PublishSagaStep.sequence,
        )
        .limit(1)
    )
    if step is not None:
        step.state = StepState.FAILED.value
        step.error_code = error_code
        step.finished_at = datetime.now(UTC)
    await _skip_pending_steps(session, job)


async def _complete_quota(session: AsyncSession, job: PublishJob) -> None:
    if job.operation not in {PublishOperation.CREATE.value, PublishOperation.UPDATE.value}:
        return
    if job.quota_completed_at is not None:
        return
    if job.quota_released_at is not None:
        raise AppError(
            "PUBLISH_QUOTA_ALREADY_RELEASED",
            "해제된 게시 한도 예약을 완료 처리할 수 없습니다.",
            409,
        )
    local_day = job.scheduled_local.date() if job.scheduled_local else datetime.now(UTC).date()
    channel = str(job.input_snapshot.get("channel", ""))
    usage = await session.scalar(
        select(PublishQuotaUsage)
        .where(
            PublishQuotaUsage.workspace_id == job.workspace_id,
            PublishQuotaUsage.connection_id == job.connection_id,
            PublishQuotaUsage.channel == channel,
            PublishQuotaUsage.local_day == local_day,
        )
        .with_for_update()
    )
    if usage is None:
        raise AppError(
            "PUBLISH_QUOTA_RESERVATION_MISSING",
            "게시 작업의 일일 한도 예약을 찾을 수 없습니다.",
            409,
        )
    usage.completed_count += 1
    job.quota_completed_at = datetime.now(UTC)


async def _cancel_job(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
    connection: PublishingConnection,
    provider: CMSProvider,
    context: ConnectionContext,
    secret: Any,
) -> tuple[str, int | None]:
    del connection
    remote_cancelled = False
    schedule_released = False
    if job.target_published_post_id is not None:
        post = await repo.published_post(job.workspace_id, job.target_published_post_id, for_update=True)
        if post.state == PublishedPostState.SCHEDULED.value:
            step = await _running_step(session, job, SagaStepKind.CANCEL_REMOTE)
            remote_call = await provider.get_post(context, secret, post.remote_id)
            await _record_call(session, job, step, remote_call)
            remote_state = remote_call.value.state.casefold()
            if remote_state in {"draft"}:
                await _finish_step(step, remote_call)
                _apply_remote(post, remote_call.value, partial=False)
                schedule_released = True
            elif remote_state in {"scheduled", "future"}:
                call = await provider.cancel_scheduled(
                    context, secret, remote_call.value
                )
                await _record_call(session, job, step, call)
                await _finish_step(step, call)
                _apply_remote(post, call.value, partial=False)
                remote_cancelled = True
                schedule_released = True
            else:
                raise ProviderFailure(
                    code="PUBLISH_SCHEDULE_ALREADY_EFFECTIVE",
                    detail="원격 예약 시각이 지나 이미 발행된 게시물은 예약 취소할 수 없습니다.",
                    retry_class=RetryClass.FINAL,
                    status_code=409,
                    method="GET",
                    endpoint_path=remote_call.endpoint_path,
                )
    job.state = JobState.CANCELLED.value
    job.cancelled_at = datetime.now(UTC)
    job.finished_at = datetime.now(UTC)
    await _cancel_pending_steps(session, job)
    await _release_quota(session, job, reverse_completed=schedule_released)
    await _notify_and_emit(
        session,
        principal,
        job,
        action="publishing.job.cancelled",
        details={
            "remote_cancelled": remote_cancelled,
            "schedule_released": schedule_released,
        },
    )
    await repo.flush("publish_job_cancellation")
    return job.state, None


async def _cancel_local_job(
    session: AsyncSession,
    repo: PublishingRepository,
    principal: Principal,
    job: PublishJob,
) -> tuple[str, int | None]:
    job.state = JobState.CANCELLED.value
    job.cancelled_at = datetime.now(UTC)
    job.finished_at = job.cancelled_at
    await _cancel_pending_steps(session, job)
    await _release_quota(session, job)
    await _notify_and_emit(
        session,
        principal,
        job,
        action="publishing.job.cancelled",
        details={"remote_cancelled": False},
    )
    await repo.flush("publish_job_cancellation")
    return job.state, None


async def _cancel_pending_steps(session: AsyncSession, job: PublishJob) -> None:
    steps = list(
        await session.scalars(
            select(PublishSagaStep).where(
                PublishSagaStep.workspace_id == job.workspace_id,
                PublishSagaStep.job_id == job.id,
                PublishSagaStep.state.in_(
                    {
                        StepState.PENDING.value,
                        StepState.RETRYING.value,
                    }
                ),
            )
        )
    )
    for step in steps:
        if step.state in {StepState.PENDING.value, StepState.RETRYING.value}:
            step.state = StepState.CANCELLED.value
            step.finished_at = job.finished_at


async def _final_failure(
    session: AsyncSession, job: PublishJob, code: str, detail: str
) -> tuple[str, int | None]:
    job.state = JobState.FINAL_FAILED.value
    job.error_code = code
    job.error_detail = detail[:4_000]
    job.finished_at = datetime.now(UTC)
    await _record_local_failure(session, job, code)
    if job.operation in {
        PublishOperation.CREATE.value,
        PublishOperation.UPDATE.value,
    } and job.quota_completed_at is None:
        await _release_quota(session, job)
    return job.state, None


async def _release_quota(
    session: AsyncSession,
    job: PublishJob,
    *,
    reverse_completed: bool = False,
) -> None:
    if job.operation not in {PublishOperation.CREATE.value, PublishOperation.UPDATE.value}:
        return
    if job.quota_released_at is not None:
        return
    local_day = job.scheduled_local.date() if job.scheduled_local else datetime.now(UTC).date()
    usage = await session.scalar(
        select(PublishQuotaUsage)
        .where(
            PublishQuotaUsage.workspace_id == job.workspace_id,
            PublishQuotaUsage.connection_id == job.connection_id,
            PublishQuotaUsage.channel == str(job.input_snapshot.get("channel", "")),
            PublishQuotaUsage.local_day == local_day,
        )
        .with_for_update()
    )
    if usage is None:
        return
    if reverse_completed and job.quota_completed_at is not None:
        usage.completed_count = max(0, usage.completed_count - 1)
        usage.reserved_count = max(0, usage.reserved_count - 1)
        job.quota_released_at = datetime.now(UTC)
    elif (
        job.quota_completed_at is None
        and usage.reserved_count > usage.completed_count
    ):
        usage.reserved_count -= 1
        job.quota_released_at = datetime.now(UTC)


async def _notify_and_emit(
    session: AsyncSession,
    principal: Principal,
    job: PublishJob,
    *,
    action: str,
    details: dict[str, Any],
) -> None:
    recipients = {job.requested_by}
    approved_by = job.input_snapshot.get("approved_by")
    if approved_by:
        try:
            recipients.add(UUID(str(approved_by)))
        except ValueError:
            pass
    session.add_all(
        [
            PublishingNotification(
                id=uuid4(),
                workspace_id=job.workspace_id,
                recipient_id=recipient,
                publish_job_id=job.id,
                naver_package_id=None,
                notification_type=action.upper().replace(".", "_"),
                payload_json={
                    "job_id": str(job.id),
                    "state": job.state,
                    **redact_metadata(details),
                },
                due_at=datetime.now(UTC),
            )
            for recipient in recipients
        ]
    )
    await _audit_outbox(
        session,
        principal,
        action=action,
        target_type="publish_job",
        target_id=job.id,
        details={"state": job.state, **redact_metadata(details)},
    )


async def _audit_outbox(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str,
    target_type: str,
    target_id: UUID,
    details: dict[str, Any],
) -> None:
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=redact_metadata(details),
    )
    await add_outbox_event(
        session,
        workspace_id=principal.workspace_id,
        aggregate_type=target_type,
        aggregate_id=str(target_id),
        event_type=action,
        schema_version=OUTBOX_SCHEMA_VERSION,
        payload={"workspace_id": str(principal.workspace_id), "actor_id": str(principal.subject_id), **redact_metadata(details)},
    )


def _connection_context(connection: PublishingConnection) -> ConnectionContext:
    return ConnectionContext(
        provider=PublishingProvider(connection.provider),
        site_url=connection.site_url,
        site_timezone=connection.site_timezone,
        remote_site_id=connection.remote_site_id,
        official_contract=connection.official_contract,
        api_version=connection.api_version,
        safe_config=connection.safe_config_json,
        site_settings=connection.site_settings_snapshot,
    )


def _worker_principal(job: PublishJob) -> Principal:
    return Principal(
        subject_id=job.requested_by,
        workspace_id=job.workspace_id,
        session_id=None,
        permissions=frozenset(),
        authentication_method="worker",
    )
