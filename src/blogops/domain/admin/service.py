"""Platform operations and tenant notification services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal, request_id_context
from blogops.core.errors import AppError
from blogops.core.retries import deterministic_jittered_delay
from blogops.db.session import apply_workspace_scope
from blogops.domain.admin.enums import (
    AdminApprovalDecision,
    AdminCommandState,
    NotificationDeliveryState,
    SupportAccessState,
)
from blogops.domain.admin.models import (
    AdminAction,
    AdminCommand,
    AdminCommandApproval,
    AdminElevationSession,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    SupportAccessRequest,
)
from blogops.domain.admin.providers import (
    AdminCommandExecutor,
    AdminOperationPolicy,
    NotificationSender,
)
from blogops.domain.admin.rules import (
    SUPPORT_ALLOWED_SCOPES,
    audit_payload_hash,
    authorize_support_scopes,
    redact_admin_metadata,
    validate_two_person_approval,
)
from blogops.domain.admin.schemas import (
    AdminCommandCreate,
    AdminCommandDecision,
    NotificationPreferenceUpsert,
    SupportAccessCreate,
    SupportAccessDecision,
)
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"
_UNSET_TARGET = object()


def _notification_retry_delay(
    policy: dict[str, Any],
    *,
    attempt_no: int,
    seed: str,
) -> int | None:
    try:
        base = int(policy["base_delay_seconds"])
        maximum = int(policy["max_delay_seconds"])
        jitter_ratio = float(policy["jitter_ratio"])
    except (KeyError, TypeError, ValueError):
        return None
    if base <= 0 or maximum < base or not 0 <= jitter_ratio <= 1:
        return None
    return deterministic_jittered_delay(
        base_seconds=base,
        maximum_seconds=maximum,
        jitter_ratio=jitter_ratio,
        attempt_no=attempt_no,
        seed=seed,
    )


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _admin_action(
        self,
        *,
        operator_id: UUID,
        target_workspace_id: UUID | None,
        action: str,
        target_type: str,
        target_id: str,
        reason: str,
        idempotency_key: str,
        metadata: dict[str, Any],
        elevation_session_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AdminAction:
        existing = await self._session.scalar(
            select(AdminAction).where(
                AdminAction.operator_id == operator_id,
                AdminAction.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        value = AdminAction(
            target_workspace_id=target_workspace_id,
            operator_id=operator_id,
            elevation_session_id=elevation_session_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            idempotency_key=idempotency_key,
            metadata_masked=redact_admin_metadata(metadata),
            before_hash=audit_payload_hash(before) if before is not None else None,
            after_hash=audit_payload_hash(after) if after is not None else None,
            request_id=request_id_context.get(),
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def create_support_access_request(
        self,
        operator: Principal,
        data: SupportAccessCreate,
        *,
        policy: AdminOperationPolicy,
    ) -> SupportAccessRequest:
        requested = frozenset(data.scopes)
        if not requested <= SUPPORT_ALLOWED_SCOPES:
            raise AppError("ADMIN_SUPPORT_SCOPE_DENIED", "지원 접근 Scope가 허용 범위를 넘었습니다.", 403)
        if "content:read_masked" in requested and not data.content_access_requested:
            raise AppError(
                "ADMIN_CONTENT_SCOPE_DECLARATION_REQUIRED",
                "본문 접근 Scope는 명시적인 본문 접근 요청으로 표시해야 합니다.",
                422,
            )
        maximum = await policy.maximum_support_minutes(scopes=requested)
        if maximum <= 0 or data.requested_minutes > maximum:
            raise AppError("ADMIN_ACCESS_DURATION_DENIED", "지원 접근 요청 시간이 정책 한도를 넘었습니다.", 403)
        existing = await self._session.scalar(
            select(SupportAccessRequest).where(
                SupportAccessRequest.target_workspace_id == data.target_workspace_id,
                SupportAccessRequest.requested_by == operator.subject_id,
                SupportAccessRequest.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            return existing
        value = SupportAccessRequest(
            target_workspace_id=data.target_workspace_id,
            requested_by=operator.subject_id,
            idempotency_key=data.idempotency_key,
            reason=data.reason,
            ticket_ref=data.ticket_ref,
            requested_scopes=sorted(requested),
            requested_minutes=data.requested_minutes,
            content_access_requested=data.content_access_requested,
        )
        self._session.add(value)
        await self._session.flush()
        await self._admin_action(
            operator_id=operator.subject_id,
            target_workspace_id=data.target_workspace_id,
            action="admin.support_access.requested",
            target_type="support_access_request",
            target_id=str(value.id),
            reason=data.reason,
            idempotency_key=f"support-request:{data.idempotency_key}",
            metadata={"ticket_ref": data.ticket_ref, "scopes": sorted(requested)},
        )
        await add_outbox_event(
            self._session,
            workspace_id=data.target_workspace_id,
            aggregate_type="support_access_request",
            aggregate_id=str(value.id),
            event_type="admin.support_access.customer_approval_required",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(data.target_workspace_id),
                "support_access_request_id": str(value.id),
                "ticket_ref": data.ticket_ref,
            },
        )
        return value

    async def decide_support_access(
        self,
        customer: Principal,
        request_id: UUID,
        data: SupportAccessDecision,
    ) -> SupportAccessRequest:
        await apply_workspace_scope(self._session, customer.workspace_id)
        value = await self._session.scalar(
            select(SupportAccessRequest)
            .where(
                SupportAccessRequest.target_workspace_id == customer.workspace_id,
                SupportAccessRequest.id == request_id,
            )
            .with_for_update()
        )
        if value is None:
            raise AppError("SUPPORT_ACCESS_NOT_FOUND", "지원 접근 요청을 찾을 수 없습니다.", 404)
        if value.state != SupportAccessState.PENDING_CUSTOMER.value:
            raise AppError("SUPPORT_ACCESS_ALREADY_DECIDED", "이미 처리된 지원 접근 요청입니다.", 409)
        if data.approve:
            if not data.approved_scopes <= set(value.requested_scopes):
                raise AppError("SUPPORT_ACCESS_SCOPE_MISMATCH", "요청하지 않은 Scope를 승인할 수 없습니다.", 422)
            if data.approve_masked_content and not value.content_access_requested:
                raise AppError(
                    "SUPPORT_ACCESS_CONTENT_NOT_REQUESTED",
                    "요청하지 않은 본문 접근을 승인할 수 없습니다.",
                    422,
                )
            scopes = authorize_support_scopes(
                requested=data.approved_scopes,
                customer_approved_content=data.approve_masked_content,
            )
            value.state = SupportAccessState.APPROVED.value
            value.customer_approved_scopes = sorted(scopes)
            value.customer_approved_content = data.approve_masked_content
            value.expires_at = datetime.now(UTC) + timedelta(minutes=value.requested_minutes)
        else:
            value.state = SupportAccessState.DENIED.value
        value.customer_approved_by = customer.subject_id
        value.customer_decision_reason = data.reason
        value.decided_at = datetime.now(UTC)
        await self._session.flush()
        await add_outbox_event(
            self._session,
            workspace_id=customer.workspace_id,
            aggregate_type="support_access_request",
            aggregate_id=str(value.id),
            event_type="admin.support_access.decided",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(customer.workspace_id),
                "support_access_request_id": str(value.id),
                "state": value.state,
            },
        )
        return value

    async def start_elevation_session(
        self, operator: Principal, request_id: UUID
    ) -> AdminElevationSession:
        request = await self._session.scalar(
            select(SupportAccessRequest)
            .where(SupportAccessRequest.id == request_id)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            request is None
            or request.state != SupportAccessState.APPROVED.value
            or request.expires_at is None
            or request.expires_at <= now
        ):
            raise AppError("SUPPORT_ACCESS_NOT_ACTIVE", "승인되고 유효한 지원 접근 요청이 필요합니다.", 403)
        if request.requested_by != operator.subject_id:
            raise AppError(
                "SUPPORT_ACCESS_OPERATOR_MISMATCH",
                "지원 접근을 요청한 운영자만 해당 세션을 시작할 수 있습니다.",
                403,
            )
        existing = await self._session.scalar(
            select(AdminElevationSession).where(
                AdminElevationSession.access_request_id == request.id,
                AdminElevationSession.operator_id == operator.subject_id,
            )
        )
        if existing is not None:
            return existing
        session = AdminElevationSession(
            access_request_id=request.id,
            target_workspace_id=request.target_workspace_id,
            operator_id=operator.subject_id,
            scopes=request.customer_approved_scopes,
            content_is_masked=True,
            expires_at=request.expires_at,
        )
        self._session.add(session)
        await self._session.flush()
        await self._admin_action(
            operator_id=operator.subject_id,
            target_workspace_id=request.target_workspace_id,
            action="admin.elevation_session.started",
            target_type="admin_elevation_session",
            target_id=str(session.id),
            reason=request.reason,
            idempotency_key=f"elevation-start:{request.id}:{operator.subject_id}",
            metadata={"scopes": session.scopes, "expires_at": session.expires_at},
            elevation_session_id=session.id,
        )
        return session

    async def create_command(
        self,
        operator: Principal,
        data: AdminCommandCreate,
        *,
        policy: AdminOperationPolicy,
    ) -> AdminCommand:
        required = await policy.required_approvals(command_kind=data.kind.value)
        if required <= 0:
            raise AppError("ADMIN_APPROVAL_POLICY_INVALID", "운영 명령 승인 수가 올바르지 않습니다.", 503)
        masked = redact_admin_metadata(data.parameters)
        request_hash = audit_payload_hash(
            {
                "target_workspace_id": data.target_workspace_id,
                "kind": data.kind.value,
                "target_type": data.target_type,
                "target_id": data.target_id,
                "parameters": masked,
                "secure_parameters_ref": data.secure_parameters_ref,
            }
        )
        existing = await self._session.scalar(
            select(AdminCommand).where(
                AdminCommand.requested_by == operator.subject_id,
                AdminCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_KEY_REUSED", "같은 운영 명령 멱등키의 요청이 다릅니다.", 409)
            return existing
        value = AdminCommand(
            target_workspace_id=data.target_workspace_id,
            requested_by=operator.subject_id,
            kind=data.kind.value,
            target_type=data.target_type,
            target_id=data.target_id,
            reason=data.reason,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            parameters_masked=masked,
            secure_parameters_ref=data.secure_parameters_ref,
            required_approvals=required,
        )
        self._session.add(value)
        await self._session.flush()
        await self._admin_action(
            operator_id=operator.subject_id,
            target_workspace_id=data.target_workspace_id,
            action="admin.command.requested",
            target_type="admin_command",
            target_id=str(value.id),
            reason=data.reason,
            idempotency_key=f"command-request:{data.idempotency_key}",
            metadata={"kind": value.kind, "parameters": masked},
        )
        return value

    async def decide_command(
        self,
        operator: Principal,
        command_id: UUID,
        data: AdminCommandDecision,
    ) -> AdminCommand:
        if not data.mfa_verified:
            raise AppError("ADMIN_MFA_REQUIRED", "운영 명령 승인에는 MFA 재확인이 필요합니다.", 403)
        command = await self._session.scalar(
            select(AdminCommand).where(AdminCommand.id == command_id).with_for_update()
        )
        if command is None:
            raise AppError("ADMIN_COMMAND_NOT_FOUND", "운영 명령을 찾을 수 없습니다.", 404)
        if command.state != AdminCommandState.PENDING_APPROVAL.value:
            raise AppError("ADMIN_COMMAND_NOT_PENDING", "승인 대기 중인 명령만 처리할 수 있습니다.", 409)
        prior = list(
            await self._session.scalars(
                select(AdminCommandApproval.approver_id).where(
                    AdminCommandApproval.command_id == command.id
                )
            )
        )
        validate_two_person_approval(
            requested_by=command.requested_by,
            approver_id=operator.subject_id,
            prior_approver_ids=prior,
        )
        approval = AdminCommandApproval(
            command_id=command.id,
            approver_id=operator.subject_id,
            decision=data.decision.value,
            reason=data.reason,
            mfa_verified=True,
        )
        self._session.add(approval)
        if data.decision == AdminApprovalDecision.REJECT:
            command.state = AdminCommandState.REJECTED.value
        else:
            command.approval_count += 1
            if command.approval_count >= command.required_approvals:
                command.state = AdminCommandState.READY.value
                await add_outbox_event(
                    self._session,
                    workspace_id=command.target_workspace_id,
                    aggregate_type="admin_command",
                    aggregate_id=str(command.id),
                    event_type="admin.command.ready",
                    schema_version=_SCHEMA_VERSION,
                    payload={
                        "admin_command_id": str(command.id),
                        "kind": command.kind,
                        "target_workspace_id": (
                            str(command.target_workspace_id)
                            if command.target_workspace_id
                            else None
                        ),
                    },
                )
        await self._session.flush()
        await self._admin_action(
            operator_id=operator.subject_id,
            target_workspace_id=command.target_workspace_id,
            action="admin.command.decided",
            target_type="admin_command",
            target_id=str(command.id),
            reason=data.reason,
            idempotency_key=f"command-decision:{command.id}:{operator.subject_id}",
            metadata={"decision": data.decision.value, "state": command.state},
        )
        return command

    async def execute_ready_command(
        self,
        command_id: UUID,
        *,
        worker_actor_id: UUID,
        executor: AdminCommandExecutor,
        expected_target_workspace_id: UUID | None | object = _UNSET_TARGET,
    ) -> AdminCommand:
        """Worker boundary; executor must use command_id as its external idempotency key."""

        command = await self._session.scalar(
            select(AdminCommand).where(AdminCommand.id == command_id).with_for_update()
        )
        if command is None:
            raise AppError("ADMIN_COMMAND_NOT_FOUND", "운영 명령을 찾을 수 없습니다.", 404)
        if (
            expected_target_workspace_id is not _UNSET_TARGET
            and command.target_workspace_id != expected_target_workspace_id
        ):
            raise AppError(
                "ADMIN_COMMAND_WORKSPACE_MISMATCH",
                "운영 명령의 대상 Workspace가 worker envelope와 다릅니다.",
                409,
            )
        if command.state in {
            AdminCommandState.SUCCEEDED.value,
            AdminCommandState.FAILED.value,
            AdminCommandState.REJECTED.value,
            AdminCommandState.CANCELLED.value,
        }:
            return command
        if command.state not in {
            AdminCommandState.READY.value,
            AdminCommandState.DISPATCHED.value,
        }:
            raise AppError("ADMIN_COMMAND_NOT_READY", "승인이 완료된 운영 명령만 실행할 수 있습니다.", 409)
        if command.state == AdminCommandState.READY.value:
            command.state = AdminCommandState.DISPATCHED.value
            command.dispatched_at = datetime.now(UTC)
            await self._session.flush()
        result = await executor.execute(
            command_id=command.id,
            kind=command.kind,
            secure_parameters_ref=command.secure_parameters_ref,
        )
        result_ref = result.result_ref.strip()
        if (
            not result_ref
            or result_ref != result.result_ref
            or len(result_ref) > 1_000
            or not isinstance(result.safe_summary, dict)
        ):
            raise AppError("ADMIN_COMMAND_RESULT_INVALID", "운영 명령 실행 증명이 없습니다.", 503)
        command.state = AdminCommandState.SUCCEEDED.value
        command.result_ref = result_ref
        command.error_code = None
        command.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._admin_action(
            operator_id=worker_actor_id,
            target_workspace_id=command.target_workspace_id,
            action="admin.command.succeeded",
            target_type="admin_command",
            target_id=str(command.id),
            reason=command.reason,
            idempotency_key=f"command-succeeded:{command.id}",
            metadata={"kind": command.kind, "result": result.safe_summary},
        )
        await add_outbox_event(
            self._session,
            workspace_id=command.target_workspace_id,
            aggregate_type="admin_command",
            aggregate_id=str(command.id),
            event_type="admin.command.succeeded",
            schema_version=_SCHEMA_VERSION,
            payload={
                "admin_command_id": str(command.id),
                "kind": command.kind,
                "target_workspace_id": (
                    str(command.target_workspace_id) if command.target_workspace_id else None
                ),
                "result_ref": command.result_ref,
            },
        )
        return command

    async def fail_ready_command(
        self,
        command_id: UUID,
        *,
        error_code: str,
        worker_actor_id: UUID | None,
        expected_target_workspace_id: UUID | None | object = _UNSET_TARGET,
    ) -> AdminCommand:
        """Persist a terminal command failure without re-executing terminal replays."""

        command = await self._session.scalar(
            select(AdminCommand).where(AdminCommand.id == command_id).with_for_update()
        )
        if command is None:
            raise AppError("ADMIN_COMMAND_NOT_FOUND", "운영 명령을 찾을 수 없습니다.", 404)
        if (
            expected_target_workspace_id is not _UNSET_TARGET
            and command.target_workspace_id != expected_target_workspace_id
        ):
            raise AppError(
                "ADMIN_COMMAND_WORKSPACE_MISMATCH",
                "운영 명령의 대상 Workspace가 worker envelope와 다릅니다.",
                409,
            )
        if command.state in {
            AdminCommandState.SUCCEEDED.value,
            AdminCommandState.FAILED.value,
            AdminCommandState.REJECTED.value,
            AdminCommandState.CANCELLED.value,
        }:
            return command
        if command.state not in {
            AdminCommandState.READY.value,
            AdminCommandState.DISPATCHED.value,
        }:
            raise AppError(
                "ADMIN_COMMAND_NOT_READY",
                "승인이 완료된 운영 명령만 실패 처리할 수 있습니다.",
                409,
            )
        command.state = AdminCommandState.FAILED.value
        command.error_code = error_code[:120] or "ADMIN_COMMAND_EXECUTION_FAILED"
        command.completed_at = datetime.now(UTC)
        await self._session.flush()
        if worker_actor_id is not None:
            await self._admin_action(
                operator_id=worker_actor_id,
                target_workspace_id=command.target_workspace_id,
                action="admin.command.failed",
                target_type="admin_command",
                target_id=str(command.id),
                reason=command.reason,
                idempotency_key=f"command-failed:{command.id}",
                metadata={"kind": command.kind, "error_code": command.error_code},
            )
        await add_outbox_event(
            self._session,
            workspace_id=command.target_workspace_id,
            aggregate_type="admin_command",
            aggregate_id=str(command.id),
            event_type="admin.command.failed",
            schema_version=_SCHEMA_VERSION,
            payload={
                "admin_command_id": str(command.id),
                "kind": command.kind,
                "target_workspace_id": (
                    str(command.target_workspace_id)
                    if command.target_workspace_id is not None
                    else None
                ),
                "error_code": command.error_code,
            },
        )
        return command

    async def execute_notification_delivery(
        self,
        workspace_id: UUID,
        delivery_id: UUID,
        *,
        sender: NotificationSender,
    ) -> NotificationDelivery:
        """Send one due delivery; delivery_id is the provider idempotency key."""

        await apply_workspace_scope(self._session, workspace_id)
        delivery = await self._session.scalar(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.workspace_id == workspace_id,
                NotificationDelivery.id == delivery_id,
            )
            .with_for_update()
        )
        if delivery is None:
            raise AppError(
                "NOTIFICATION_DELIVERY_NOT_FOUND",
                "알림 전달 작업을 찾을 수 없습니다.",
                404,
            )
        if delivery.state in {
            NotificationDeliveryState.SENT.value,
            NotificationDeliveryState.FAILED.value,
            NotificationDeliveryState.BOUNCED.value,
            NotificationDeliveryState.SUPPRESSED.value,
        }:
            return delivery
        if delivery.state != NotificationDeliveryState.PENDING.value:
            raise AppError(
                "NOTIFICATION_DELIVERY_STATE_INVALID",
                "대기 중인 알림 전달만 실행할 수 있습니다.",
                409,
            )
        now = datetime.now(UTC)
        if delivery.next_attempt_at > now:
            return delivery
        if delivery.attempt_count >= delivery.max_attempts:
            delivery.state = NotificationDeliveryState.FAILED.value
            delivery.failed_at = now
            delivery.error_code = "NOTIFICATION_MAX_ATTEMPTS_EXCEEDED"
            await add_outbox_event(
                self._session,
                workspace_id=workspace_id,
                aggregate_type="notification_delivery",
                aggregate_id=str(delivery.id),
                event_type="admin.notification_delivery.failed",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(workspace_id),
                    "notification_delivery_id": str(delivery.id),
                    "error_code": delivery.error_code,
                },
            )
            await self._session.flush()
            return delivery
        delivery.attempt_count += 1
        await self._session.flush()
        raw_provider_message_ref = await sender.send(delivery_id=delivery.id)
        provider_message_ref = raw_provider_message_ref.strip()
        if (
            not provider_message_ref
            or provider_message_ref != raw_provider_message_ref
            or len(provider_message_ref) > 500
        ):
            raise AppError(
                "NOTIFICATION_SENDER_RESULT_INVALID",
                "알림 공급자의 전송 증명이 올바르지 않습니다.",
                503,
            )
        delivery.state = NotificationDeliveryState.SENT.value
        delivery.sent_at = now
        delivery.failed_at = None
        delivery.provider_message_ref = provider_message_ref
        delivery.error_code = None
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="notification_delivery",
            aggregate_id=str(delivery.id),
            event_type="admin.notification_delivery.sent",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "notification_delivery_id": str(delivery.id),
                "channel": delivery.channel,
                "provider_message_ref": provider_message_ref,
            },
        )
        await self._session.flush()
        return delivery

    async def fail_notification_delivery(
        self,
        workspace_id: UUID,
        delivery_id: UUID,
        *,
        error_code: str,
        retryable: bool,
    ) -> NotificationDelivery:
        """Record a failed attempt and either schedule backoff or terminate."""

        await apply_workspace_scope(self._session, workspace_id)
        delivery = await self._session.scalar(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.workspace_id == workspace_id,
                NotificationDelivery.id == delivery_id,
            )
            .with_for_update()
        )
        if delivery is None:
            raise AppError(
                "NOTIFICATION_DELIVERY_NOT_FOUND",
                "알림 전달 작업을 찾을 수 없습니다.",
                404,
            )
        if delivery.state in {
            NotificationDeliveryState.SENT.value,
            NotificationDeliveryState.FAILED.value,
            NotificationDeliveryState.BOUNCED.value,
            NotificationDeliveryState.SUPPRESSED.value,
        }:
            return delivery
        if delivery.state != NotificationDeliveryState.PENDING.value:
            raise AppError(
                "NOTIFICATION_DELIVERY_STATE_INVALID",
                "대기 중인 알림 전달만 실패 처리할 수 있습니다.",
                409,
            )
        if delivery.attempt_count == 0:
            delivery.attempt_count = 1
        now = datetime.now(UTC)
        safe_code = error_code[:120] or "NOTIFICATION_DELIVERY_FAILED"
        should_retry = retryable and delivery.attempt_count < delivery.max_attempts
        delay = (
            _notification_retry_delay(
                delivery.retry_policy_snapshot,
                attempt_no=delivery.attempt_count,
                seed=str(delivery.id),
            )
            if should_retry
            else None
        )
        if not should_retry or delay is None:
            delivery.state = NotificationDeliveryState.FAILED.value
            delivery.failed_at = now
            delivery.error_code = (
                "NOTIFICATION_RETRY_POLICY_INVALID"
                if should_retry and delay is None
                else safe_code
            )
            event_type = "admin.notification_delivery.failed"
            payload = {
                "workspace_id": str(workspace_id),
                "notification_delivery_id": str(delivery.id),
                "error_code": delivery.error_code,
            }
        else:
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.failed_at = None
            delivery.error_code = safe_code
            event_type = "admin.notification_delivery.retry_scheduled"
            payload = {
                "workspace_id": str(workspace_id),
                "notification_delivery_id": str(delivery.id),
                "next_attempt_at": delivery.next_attempt_at.isoformat(),
                "error_code": delivery.error_code,
            }
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="notification_delivery",
            aggregate_id=str(delivery.id),
            event_type=event_type,
            schema_version=_SCHEMA_VERSION,
            payload=payload,
        )
        await self._session.flush()
        return delivery

    async def upsert_notification_preference(
        self, principal: Principal, data: NotificationPreferenceUpsert
    ) -> NotificationPreference:
        await apply_workspace_scope(self._session, principal.workspace_id)
        value = await self._session.scalar(
            select(NotificationPreference)
            .where(
                NotificationPreference.workspace_id == principal.workspace_id,
                NotificationPreference.user_id == principal.subject_id,
                NotificationPreference.event_type == data.event_type,
                NotificationPreference.channel == data.channel.value,
            )
            .with_for_update()
        )
        if value is None:
            value = NotificationPreference(
                workspace_id=principal.workspace_id,
                user_id=principal.subject_id,
                event_type=data.event_type,
                channel=data.channel.value,
                frequency=data.frequency.value,
                digest_hour=data.digest_hour,
                timezone=data.timezone,
                quiet_hours=data.quiet_hours,
            )
            self._session.add(value)
        else:
            value.frequency = data.frequency.value
            value.digest_hour = data.digest_hour
            value.timezone = data.timezone
            value.quiet_hours = data.quiet_hours
        await self._session.flush()
        return value

    async def list_notifications(
        self, principal: Principal, *, limit: int, offset: int
    ) -> list[Notification]:
        await apply_workspace_scope(self._session, principal.workspace_id)
        return list(
            await self._session.scalars(
                select(Notification)
                .where(
                    Notification.workspace_id == principal.workspace_id,
                    Notification.recipient_user_id == principal.subject_id,
                )
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    async def mark_notification_read(
        self, principal: Principal, notification_id: UUID
    ) -> Notification:
        await apply_workspace_scope(self._session, principal.workspace_id)
        value = await self._session.scalar(
            select(Notification)
            .where(
                Notification.workspace_id == principal.workspace_id,
                Notification.recipient_user_id == principal.subject_id,
                Notification.id == notification_id,
            )
            .with_for_update()
        )
        if value is None:
            raise AppError("NOTIFICATION_NOT_FOUND", "알림을 찾을 수 없습니다.", 404)
        if value.read_at is None:
            value.read_at = datetime.now(UTC)
            await self._session.flush()
        return value

    async def snooze_notification(
        self, principal: Principal, notification_id: UUID, *, until: datetime
    ) -> Notification:
        if until.tzinfo is None or until <= datetime.now(UTC):
            raise AppError("NOTIFICATION_SNOOZE_INVALID", "재알림 시각은 미래여야 합니다.", 422)
        await apply_workspace_scope(self._session, principal.workspace_id)
        value = await self._session.scalar(
            select(Notification)
            .where(
                Notification.workspace_id == principal.workspace_id,
                Notification.recipient_user_id == principal.subject_id,
                Notification.id == notification_id,
            )
            .with_for_update()
        )
        if value is None:
            raise AppError("NOTIFICATION_NOT_FOUND", "알림을 찾을 수 없습니다.", 404)
        value.snoozed_until = until
        await self._session.flush()
        return value
