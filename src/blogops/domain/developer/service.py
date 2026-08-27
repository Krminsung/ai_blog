"""Developer credential and webhook lifecycle services."""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal, PrincipalKind
from blogops.core.errors import AppError
from blogops.core.serialization import canonical_json_hash as _canonical_hash
from blogops.db.session import apply_workspace_scope
from blogops.domain.developer.enums import (
    ApiKeyState,
    WebhookAttemptOutcome,
    WebhookDeliveryState,
    WebhookEndpointState,
)
from blogops.domain.developer.models import (
    ApiKey,
    ApiRateLimitPolicy,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
    WebhookEvent,
)
from blogops.domain.developer.providers import (
    ApiKeySecrets,
    DnsResolver,
    PrivateWebhookPayloads,
    RateLimitStore,
    WebhookOwnershipVerifier,
    WebhookTransport,
    WebhookTransportRequest,
    WebhookTransportResult,
    WorkspaceApiPolicy,
)
from blogops.domain.developer.schemas import (
    ApiKeyCreate,
    ApiKeyRotate,
    RateLimitPolicyCreate,
    WebhookEndpointCreate,
    WebhookEventCreate,
)
from blogops.domain.developer.security import (
    RateLimitRule,
    authorize_key_scopes,
    ip_is_allowed,
    issue_api_key,
    required_rate_limit_rules,
    validate_webhook_destination,
    verify_api_key,
    webhook_signature,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"
_SENSITIVE_PREVIEW_KEYS = frozenset(
    {"password", "secret", "token", "api_key", "authorization", "cookie", "email", "phone"}
)


def _mask_preview(value: Any, key: str | None = None) -> Any:
    if key:
        normalized = key.casefold().replace("-", "_").replace(".", "_")
        if frozenset(normalized.split("_")).intersection(_SENSITIVE_PREVIEW_KEYS):
            return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _mask_preview(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_preview(item) for item in value]
    return value


def _retry_delay(policy: dict[str, Any], *, attempt_no: int, seed: str) -> int:
    required = {"base_delay_seconds", "max_delay_seconds", "jitter_ratio"}
    if not required <= policy.keys():
        raise AppError("WEBHOOK_RETRY_POLICY_INCOMPLETE", "Webhook 재시도 정책이 불완전합니다.", 503)
    base = int(policy["base_delay_seconds"])
    maximum = int(policy["max_delay_seconds"])
    jitter_ratio = float(policy["jitter_ratio"])
    if base <= 0 or maximum < base or not 0 <= jitter_ratio <= 1:
        raise AppError("WEBHOOK_RETRY_POLICY_INVALID", "Webhook 재시도 정책이 올바르지 않습니다.", 503)
    delay = min(maximum, base * (2 ** max(0, attempt_no - 1)))
    digest = int(hashlib.sha256(f"{seed}:{attempt_no}".encode()).hexdigest()[:8], 16)
    unit = digest / 0xFFFFFFFF
    return max(1, round(delay * (1 - jitter_ratio + 2 * jitter_ratio * unit)))


class DeveloperService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _record(
        self,
        *,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self._session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(target_id),
                **details,
            },
        )

    async def create_api_key(
        self,
        principal: Principal,
        data: ApiKeyCreate,
        *,
        secrets_provider: ApiKeySecrets,
        policy: WorkspaceApiPolicy,
    ) -> tuple[ApiKey, str]:
        await self._scope(principal.workspace_id)
        if data.expires_at is not None and data.expires_at <= datetime.now(UTC):
            raise AppError("API_KEY_EXPIRY_INVALID", "API Key 만료 시각은 미래여야 합니다.", 422)
        allowed = await policy.allowed_scopes(principal.workspace_id)
        scopes = authorize_key_scopes(
            requested=data.scopes,
            actor_permissions=principal.permissions,
            workspace_scopes=allowed,
        )
        key_version, pepper = await secrets_provider.pepper()
        material = issue_api_key(environment=data.environment.value, pepper=pepper)
        value = ApiKey(
            workspace_id=principal.workspace_id,
            name=data.name.strip(),
            prefix=material.prefix,
            secret_digest=material.digest,
            hash_key_version=key_version,
            environment=data.environment.value,
            scopes=sorted(scopes),
            ip_allowlist=data.ip_allowlist,
            endpoint_allowlist=sorted(set(data.endpoint_allowlist)),
            expires_at=data.expires_at,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="developer.api_key.created",
            target_type="api_key",
            target_id=value.id,
            details={"prefix": value.prefix, "scopes": value.scopes},
        )
        return value, material.raw

    async def rotate_api_key(
        self,
        principal: Principal,
        key_id: UUID,
        data: ApiKeyRotate,
        *,
        secrets_provider: ApiKeySecrets,
        policy: WorkspaceApiPolicy,
    ) -> tuple[ApiKey, str]:
        await self._scope(principal.workspace_id)
        old = await self._session.scalar(
            select(ApiKey)
            .where(ApiKey.workspace_id == principal.workspace_id, ApiKey.id == key_id)
            .with_for_update()
        )
        if old is None:
            raise AppError("API_KEY_NOT_FOUND", "API Key를 찾을 수 없습니다.", 404)
        if old.state != ApiKeyState.ACTIVE.value:
            raise AppError("API_KEY_NOT_ACTIVE", "활성 API Key만 회전할 수 있습니다.", 409)
        if data.expires_at is not None and data.expires_at <= datetime.now(UTC):
            raise AppError("API_KEY_EXPIRY_INVALID", "API Key 만료 시각은 미래여야 합니다.", 422)
        requested = data.scopes if data.scopes is not None else set(old.scopes)
        allowed = await policy.allowed_scopes(principal.workspace_id)
        scopes = authorize_key_scopes(
            requested=requested,
            actor_permissions=principal.permissions,
            workspace_scopes=allowed,
        )
        key_version, pepper = await secrets_provider.pepper()
        material = issue_api_key(environment=old.environment, pepper=pepper)
        replacement = ApiKey(
            workspace_id=principal.workspace_id,
            name=old.name,
            prefix=material.prefix,
            secret_digest=material.digest,
            hash_key_version=key_version,
            environment=old.environment,
            scopes=sorted(scopes),
            ip_allowlist=old.ip_allowlist,
            endpoint_allowlist=old.endpoint_allowlist,
            generation=old.generation + 1,
            rotated_from_id=old.id,
            expires_at=data.expires_at if data.expires_at is not None else old.expires_at,
            created_by=principal.subject_id,
        )
        self._session.add(replacement)
        await self._session.flush()
        old.state = ApiKeyState.ROTATED.value
        old.rotated_to_id = replacement.id
        old.revoked_at = datetime.now(UTC)
        old.revocation_reason = data.reason
        await self._session.flush()
        await self._record(
            principal=principal,
            action="developer.api_key.rotated",
            target_type="api_key",
            target_id=old.id,
            details={
                "replacement_id": str(replacement.id),
                "replacement_prefix": replacement.prefix,
            },
        )
        return replacement, material.raw

    async def revoke_api_key(self, principal: Principal, key_id: UUID, *, reason: str) -> ApiKey:
        await self._scope(principal.workspace_id)
        value = await self._session.scalar(
            select(ApiKey)
            .where(ApiKey.workspace_id == principal.workspace_id, ApiKey.id == key_id)
            .with_for_update()
        )
        if value is None:
            raise AppError("API_KEY_NOT_FOUND", "API Key를 찾을 수 없습니다.", 404)
        if value.state == ApiKeyState.REVOKED.value:
            return value
        if value.state != ApiKeyState.ACTIVE.value:
            raise AppError("API_KEY_NOT_ACTIVE", "활성 API Key만 폐기할 수 있습니다.", 409)
        value.state = ApiKeyState.REVOKED.value
        value.revoked_at = datetime.now(UTC)
        value.revocation_reason = reason
        await self._session.flush()
        await self._record(
            principal=principal,
            action="developer.api_key.revoked",
            target_type="api_key",
            target_id=value.id,
            details={"prefix": value.prefix, "reason": reason},
        )
        return value

    async def authenticate_api_key(
        self,
        raw_key: str,
        *,
        secrets_provider: ApiKeySecrets,
        remote_address: str,
        endpoint: str,
        expected_workspace_id: UUID | None = None,
    ) -> Principal:
        """Resolve a globally unique prefix, verify its hash, then bind tenant scope."""

        prefix = raw_key[:18]
        workspace_id = await self._session.scalar(
            text("SELECT app.resolve_api_key_workspace(:prefix)"),
            {"prefix": prefix},
        )
        if workspace_id is None:
            raise AppError("API_KEY_INVALID", "API Key가 올바르지 않습니다.", 401)
        await self._scope(workspace_id)
        value = await self._session.scalar(
            select(ApiKey).where(
                ApiKey.workspace_id == workspace_id,
                ApiKey.prefix == prefix,
            )
        )
        if value is None:
            raise AppError("API_KEY_INVALID", "API Key가 올바르지 않습니다.", 401)
        key_version, pepper = await secrets_provider.pepper(value.hash_key_version)
        if key_version != value.hash_key_version or not verify_api_key(
            raw_key, value.secret_digest, pepper=pepper
        ):
            raise AppError("API_KEY_INVALID", "API Key가 올바르지 않습니다.", 401)
        now = datetime.now(UTC)
        if value.state != ApiKeyState.ACTIVE.value or (
            value.expires_at is not None and value.expires_at <= now
        ):
            raise AppError("API_KEY_INACTIVE", "API Key가 만료되었거나 폐기되었습니다.", 401)
        if expected_workspace_id is not None and value.workspace_id != expected_workspace_id:
            raise AppError("API_KEY_INVALID", "API Key가 올바르지 않습니다.", 401)
        if not ip_is_allowed(remote_address, value.ip_allowlist):
            raise AppError("API_KEY_IP_DENIED", "이 IP에서는 API Key를 사용할 수 없습니다.", 403)
        if value.endpoint_allowlist and not any(
            fnmatchcase(endpoint, pattern) for pattern in value.endpoint_allowlist
        ):
            raise AppError("API_KEY_ENDPOINT_DENIED", "이 Endpoint에는 API Key를 사용할 수 없습니다.", 403)
        value.last_used_at = now
        value.last_used_ip_hash = hmac.new(
            pepper,
            remote_address.encode(),
            hashlib.sha256,
        ).hexdigest()
        await self._session.flush()
        return Principal(
            subject_id=value.id,
            workspace_id=value.workspace_id,
            session_id=None,
            permissions=frozenset(value.scopes),
            authentication_method="api_key",
            kind=PrincipalKind.API_KEY,
        )

    async def list_api_keys(self, principal: Principal) -> list[ApiKey]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(ApiKey)
                .where(ApiKey.workspace_id == principal.workspace_id)
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
            )
        )

    async def enforce_api_rate_limit(
        self,
        principal: Principal,
        *,
        api_key_id: UUID,
        endpoint: str,
        request_id: str,
        store: RateLimitStore,
    ) -> dict[str, int]:
        await self._scope(principal.workspace_id)
        now = datetime.now(UTC)
        values = list(
            await self._session.scalars(
                select(ApiRateLimitPolicy).where(
                    ApiRateLimitPolicy.workspace_id == principal.workspace_id,
                    ApiRateLimitPolicy.active_from <= now,
                    (
                        ApiRateLimitPolicy.active_until.is_(None)
                        | (ApiRateLimitPolicy.active_until > now)
                    ),
                )
            )
        )

        def newest(kind: str, scope_ref: str) -> ApiRateLimitPolicy | None:
            matches = [
                value
                for value in values
                if value.scope_kind == kind
                and value.scope_ref == scope_ref
                and fnmatchcase(endpoint, value.endpoint_pattern)
            ]
            return max(matches, key=lambda value: value.version, default=None)

        workspace_policy = newest("WORKSPACE", str(principal.workspace_id))
        endpoint_policy = newest("ENDPOINT", endpoint)
        key_policy = newest("KEY", str(api_key_id))

        def rule(value: ApiRateLimitPolicy | None) -> RateLimitRule | None:
            if value is None:
                return None
            return RateLimitRule(
                identity=str(value.id),
                limit=value.request_limit,
                window_seconds=value.window_seconds,
                burst=value.burst,
                concurrent_limit=value.concurrent_limit,
            )

        rules = required_rate_limit_rules(
            workspace_rule=rule(workspace_policy),
            endpoint_rule=rule(endpoint_policy),
            key_rule=rule(key_policy),
        )
        allowed, counters = await store.consume(rules=rules, request_id=request_id)
        if not allowed:
            raise AppError(
                "API_RATE_LIMIT_EXCEEDED",
                "API 요청 한도를 초과했습니다.",
                429,
                remediation={"rate_limits": counters},
            )
        return counters

    async def create_rate_limit_policy(
        self, principal: Principal, data: RateLimitPolicyCreate
    ) -> ApiRateLimitPolicy:
        await self._scope(principal.workspace_id)
        payload = data.model_dump(mode="json")
        value = ApiRateLimitPolicy(
            workspace_id=principal.workspace_id,
            scope_kind=data.scope_kind,
            scope_ref=data.scope_ref,
            endpoint_pattern=data.endpoint_pattern,
            version=data.version,
            request_limit=data.request_limit,
            window_seconds=data.window_seconds,
            burst=data.burst,
            concurrent_limit=data.concurrent_limit,
            policy_hash=_canonical_hash(payload),
            active_from=data.active_from,
            active_until=data.active_until,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="developer.rate_limit_policy.created",
            target_type="api_rate_limit_policy",
            target_id=value.id,
            details={"scope_kind": value.scope_kind, "version": value.version},
        )
        return value

    async def register_webhook(
        self,
        principal: Principal,
        data: WebhookEndpointCreate,
        *,
        policy: WorkspaceApiPolicy,
        dns: DnsResolver,
        verifier: WebhookOwnershipVerifier,
    ) -> WebhookEndpoint:
        await self._scope(principal.workspace_id)
        allowed_events = await policy.allowed_webhook_events(principal.workspace_id)
        if not data.event_types <= allowed_events:
            raise AppError("WEBHOOK_EVENT_SCOPE_DENIED", "허용되지 않은 Webhook 이벤트가 포함되었습니다.", 403)
        preliminary = urlsplit(data.url)
        if not preliminary.hostname:
            raise AppError("SOURCE_URL_INVALID", "Webhook URL에 호스트가 없습니다.", 422)
        addresses = await dns.resolve_public(preliminary.hostname)
        normalized = validate_webhook_destination(data.url, resolved_addresses=addresses)
        hostname = urlsplit(normalized).hostname
        if hostname is None:
            raise AppError("SOURCE_URL_INVALID", "Webhook URL에 호스트가 없습니다.", 422)
        ownership_receipt = await verifier.verify(
            url=normalized,
            resolved_addresses=tuple(addresses),
        )
        if not ownership_receipt:
            raise AppError("WEBHOOK_OWNERSHIP_UNVERIFIED", "Endpoint 소유권 검증에 실패했습니다.", 422)
        value = WebhookEndpoint(
            workspace_id=principal.workspace_id,
            name=data.name,
            normalized_url=normalized,
            hostname=hostname,
            event_types=sorted(data.event_types),
            secret_ref=data.secret_ref,
            secret_version=data.secret_version,
            state=WebhookEndpointState.ACTIVE.value,
            verification_challenge_digest=hashlib.sha256(ownership_receipt.encode()).hexdigest(),
            verified_at=datetime.now(UTC),
            failure_disable_threshold=data.failure_disable_threshold,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="developer.webhook_endpoint.verified",
            target_type="webhook_endpoint",
            target_id=value.id,
            details={"hostname": hostname, "event_types": value.event_types},
        )
        return value

    async def create_webhook_event(
        self, principal: Principal, data: WebhookEventCreate
    ) -> tuple[WebhookEvent, list[WebhookDelivery]]:
        await self._scope(principal.workspace_id)
        if (
            not data.payload_object_ref.startswith(
                f"workspaces/{principal.workspace_id}/webhooks/"
            )
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,999}", data.payload_object_ref)
            or ".." in data.payload_object_ref
            or "//" in data.payload_object_ref
        ):
            raise AppError(
                "WEBHOOK_PAYLOAD_REF_INVALID",
                "Webhook Payload 참조가 Workspace와 일치하지 않습니다.",
                422,
            )
        existing = await self._session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.workspace_id == principal.workspace_id,
                WebhookEvent.source_event_id == data.source_event_id,
            )
        )
        if existing is not None:
            if existing.payload_hash != data.payload_hash or existing.event_type != data.event_type:
                raise AppError("WEBHOOK_EVENT_REUSED", "같은 이벤트 ID의 Payload가 다릅니다.", 409)
            deliveries = list(
                await self._session.scalars(
                    select(WebhookDelivery).where(
                        WebhookDelivery.workspace_id == principal.workspace_id,
                        WebhookDelivery.event_id == existing.id,
                    )
                )
            )
            return existing, deliveries
        event = WebhookEvent(
            workspace_id=principal.workspace_id,
            source_event_id=data.source_event_id,
            event_type=data.event_type,
            schema_version=data.schema_version,
            payload_object_ref=data.payload_object_ref,
            payload_hash=data.payload_hash,
            payload_preview=_mask_preview(data.payload_preview),
            occurred_at=data.occurred_at,
        )
        self._session.add(event)
        await self._session.flush()
        endpoints = list(
            await self._session.scalars(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.workspace_id == principal.workspace_id,
                    WebhookEndpoint.state == WebhookEndpointState.ACTIVE.value,
                )
            )
        )
        now = datetime.now(UTC)
        deliveries: list[WebhookDelivery] = []
        for endpoint in endpoints:
            if data.event_type not in endpoint.event_types:
                continue
            delivery = WebhookDelivery(
                workspace_id=principal.workspace_id,
                endpoint_id=endpoint.id,
                event_id=event.id,
                retry_policy_snapshot=data.retry_policy,
                max_attempts=data.max_attempts,
                manual_replay_limit=data.manual_replay_limit,
                next_attempt_at=now,
            )
            self._session.add(delivery)
            deliveries.append(delivery)
        await self._session.flush()
        for delivery in deliveries:
            await add_outbox_event(
                self._session,
                workspace_id=principal.workspace_id,
                aggregate_type="webhook_delivery",
                aggregate_id=str(delivery.id),
                event_type="developer.webhook_delivery.queued",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(principal.workspace_id),
                    "webhook_delivery_id": str(delivery.id),
                    "next_attempt_at": delivery.next_attempt_at.isoformat(),
                },
            )
        await self._record(
            principal=principal,
            action="developer.webhook_event.queued",
            target_type="webhook_event",
            target_id=event.id,
            details={"event_type": event.event_type, "delivery_count": len(deliveries)},
        )
        return event, deliveries

    async def execute_delivery(
        self,
        workspace_id: UUID,
        delivery_id: UUID,
        *,
        secrets_provider: ApiKeySecrets,
        payloads: PrivateWebhookPayloads,
        dns: DnsResolver,
        transport: WebhookTransport,
    ) -> WebhookDelivery:
        """Worker boundary: DNS is re-resolved and validated immediately before every send."""

        await self._scope(workspace_id)
        delivery = await self._session.scalar(
            select(WebhookDelivery)
            .where(WebhookDelivery.workspace_id == workspace_id, WebhookDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            raise AppError("WEBHOOK_DELIVERY_NOT_FOUND", "Webhook 전달 작업을 찾을 수 없습니다.", 404)
        if delivery.state not in {
            WebhookDeliveryState.PENDING.value,
            WebhookDeliveryState.RETRY_WAIT.value,
        }:
            return delivery
        now = datetime.now(UTC)
        if delivery.next_attempt_at > now:
            return delivery
        endpoint = await self._session.scalar(
            select(WebhookEndpoint)
            .where(
                WebhookEndpoint.workspace_id == workspace_id,
                WebhookEndpoint.id == delivery.endpoint_id,
            )
            .with_for_update()
        )
        event = await self._session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.workspace_id == workspace_id,
                WebhookEvent.id == delivery.event_id,
            )
        )
        if endpoint is None or event is None:
            raise AppError("WEBHOOK_DELIVERY_REFERENCE_MISSING", "Webhook 전달 참조가 손상되었습니다.", 500)
        if endpoint.state != WebhookEndpointState.ACTIVE.value:
            delivery.state = WebhookDeliveryState.CANCELLED.value
            delivery.last_error_code = "WEBHOOK_ENDPOINT_DISABLED"
            await add_outbox_event(
                self._session,
                workspace_id=workspace_id,
                aggregate_type="webhook_delivery",
                aggregate_id=str(delivery.id),
                event_type="developer.webhook_delivery.cancelled",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(workspace_id),
                    "webhook_delivery_id": str(delivery.id),
                    "error_code": delivery.last_error_code,
                },
            )
            await self._session.flush()
            return delivery
        timestamp = int(now.timestamp())
        addresses: list[str] = []
        try:
            addresses = await dns.resolve_public(endpoint.hostname)
            validate_webhook_destination(
                endpoint.normalized_url,
                resolved_addresses=addresses,
            )
            body = await payloads.read(event.payload_object_ref)
            if hashlib.sha256(body).hexdigest() != event.payload_hash:
                raise AppError(
                    "WEBHOOK_PAYLOAD_HASH_MISMATCH",
                    "Webhook Payload 무결성 검증에 실패했습니다.",
                    500,
                )
            secret = await secrets_provider.webhook_secret(
                endpoint.secret_ref,
                endpoint.secret_version,
            )
            headers = {
                "content-type": "application/json",
                "x-blogops-event-id": event.source_event_id,
                "x-blogops-event-type": event.event_type,
                "x-blogops-schema-version": event.schema_version,
                "x-blogops-timestamp": str(timestamp),
                "x-blogops-signature": webhook_signature(
                    secret=secret,
                    timestamp=timestamp,
                    body=body,
                ),
            }
            result = await transport.send(
                WebhookTransportRequest(
                    url=endpoint.normalized_url,
                    resolved_addresses=tuple(addresses),
                    headers=headers,
                    body=body,
                    allow_redirects=False,
                )
            )
            if (
                not isinstance(result, WebhookTransportResult)
                or result.duration_ms < 0
                or (
                    result.status_code is not None
                    and not 100 <= result.status_code <= 599
                )
                or not isinstance(result.headers_masked, dict)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in result.headers_masked.items()
                )
                or (
                    result.transport_error_code is not None
                    and (
                        not isinstance(result.transport_error_code, str)
                        or not result.transport_error_code
                        or len(result.transport_error_code) > 120
                    )
                )
                or (
                    result.body_hash is not None
                    and re.fullmatch(r"[a-f0-9]{64}", result.body_hash) is None
                )
            ):
                raise AppError(
                    "WEBHOOK_TRANSPORT_RESULT_INVALID",
                    "Webhook 전송 결과가 안전한 계약과 일치하지 않습니다.",
                    503,
                )
        except AppError as exc:
            result = WebhookTransportResult(
                status_code=None,
                headers_masked={},
                body_hash=None,
                body_preview_masked=None,
                duration_ms=0,
                transport_error_code=exc.code[:120],
            )
        except Exception:
            result = WebhookTransportResult(
                status_code=None,
                headers_masked={},
                body_hash=None,
                body_preview_masked=None,
                duration_ms=0,
                transport_error_code="WEBHOOK_DELIVERY_EXECUTION_FAILED",
            )
        policy = delivery.retry_policy_snapshot
        required = {"success_status_min", "success_status_max", "retryable_statuses"}
        if not required <= policy.keys():
            raise AppError("WEBHOOK_RETRY_POLICY_INCOMPLETE", "Webhook 응답 정책이 불완전합니다.", 503)
        attempt_no = delivery.attempt_count + 1
        cycle_attempt_no = delivery.cycle_attempt_count + 1
        success = (
            result.status_code is not None
            and int(policy["success_status_min"])
            <= result.status_code
            <= int(policy["success_status_max"])
        )
        retryable = result.transport_error_code is not None or (
            result.status_code in {int(value) for value in policy["retryable_statuses"]}
        )
        if success:
            outcome = WebhookAttemptOutcome.SUCCEEDED.value
        elif retryable and cycle_attempt_no < delivery.max_attempts:
            outcome = WebhookAttemptOutcome.RETRYABLE_FAILURE.value
        else:
            outcome = WebhookAttemptOutcome.FINAL_FAILURE.value
        self._session.add(
            WebhookDeliveryAttempt(
                workspace_id=workspace_id,
                delivery_id=delivery.id,
                attempt_no=attempt_no,
                delivery_cycle=delivery.manual_replay_count,
                cycle_attempt_no=cycle_attempt_no,
                event_external_id=event.source_event_id,
                destination_url=endpoint.normalized_url,
                resolved_addresses=addresses,
                signature_version="v1",
                secret_version=endpoint.secret_version,
                request_timestamp=timestamp,
                outcome=outcome,
                response_status=result.status_code,
                response_headers_masked=result.headers_masked,
                response_body_hash=result.body_hash,
                response_body_preview_masked=result.body_preview_masked,
                duration_ms=result.duration_ms,
                error_code=result.transport_error_code,
            )
        )
        delivery.attempt_count = attempt_no
        delivery.cycle_attempt_count = cycle_attempt_no
        if success:
            delivery.state = WebhookDeliveryState.DELIVERED.value
            delivery.delivered_at = now
            delivery.last_error_code = None
            endpoint.failure_count = 0
        elif outcome == WebhookAttemptOutcome.RETRYABLE_FAILURE.value:
            delivery.state = WebhookDeliveryState.RETRY_WAIT.value
            delivery.last_error_code = result.transport_error_code or f"HTTP_{result.status_code}"
            delivery.next_attempt_at = now + timedelta(
                seconds=_retry_delay(
                    policy,
                    attempt_no=cycle_attempt_no,
                    seed=f"{delivery.id}:{delivery.manual_replay_count}",
                )
            )
            endpoint.failure_count += 1
        else:
            delivery.state = WebhookDeliveryState.DEAD_LETTERED.value
            delivery.dead_lettered_at = now
            delivery.last_error_code = result.transport_error_code or f"HTTP_{result.status_code}"
            endpoint.failure_count += 1
        if delivery.state == WebhookDeliveryState.DELIVERED.value:
            await add_outbox_event(
                self._session,
                workspace_id=workspace_id,
                aggregate_type="webhook_delivery",
                aggregate_id=str(delivery.id),
                event_type="developer.webhook_delivery.delivered",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(workspace_id),
                    "webhook_delivery_id": str(delivery.id),
                    "webhook_event_id": str(event.id),
                },
            )
        elif delivery.state == WebhookDeliveryState.RETRY_WAIT.value:
            await add_outbox_event(
                self._session,
                workspace_id=workspace_id,
                aggregate_type="webhook_delivery",
                aggregate_id=str(delivery.id),
                event_type="developer.webhook_delivery.retry_scheduled",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(workspace_id),
                    "webhook_delivery_id": str(delivery.id),
                    "next_attempt_at": delivery.next_attempt_at.isoformat(),
                    "error_code": delivery.last_error_code,
                },
            )
        if endpoint.failure_count >= endpoint.failure_disable_threshold:
            was_active = endpoint.state == WebhookEndpointState.ACTIVE.value
            endpoint.state = WebhookEndpointState.DISABLED.value
            endpoint.disabled_at = now
            endpoint.disabled_reason = "CONSECUTIVE_DELIVERY_FAILURES"
            if was_active:
                await add_outbox_event(
                    self._session,
                    workspace_id=workspace_id,
                    aggregate_type="webhook_endpoint",
                    aggregate_id=str(endpoint.id),
                    event_type="developer.webhook_endpoint.disabled",
                    schema_version=_SCHEMA_VERSION,
                    payload={
                        "workspace_id": str(workspace_id),
                        "webhook_endpoint_id": str(endpoint.id),
                        "reason": endpoint.disabled_reason,
                        "failure_count": endpoint.failure_count,
                    },
                )
        if delivery.state == WebhookDeliveryState.DEAD_LETTERED.value:
            await add_outbox_event(
                self._session,
                workspace_id=workspace_id,
                aggregate_type="webhook_delivery",
                aggregate_id=str(delivery.id),
                event_type="developer.webhook_delivery.dead_lettered",
                schema_version=_SCHEMA_VERSION,
                payload={
                    "workspace_id": str(workspace_id),
                    "webhook_delivery_id": str(delivery.id),
                    "webhook_event_id": str(event.id),
                    "error_code": delivery.last_error_code,
                },
            )
        await self._session.flush()
        return delivery

    async def fail_delivery(
        self,
        workspace_id: UUID,
        delivery_id: UUID,
        *,
        error_code: str,
    ) -> WebhookDelivery:
        """Dead-letter a corrupt worker envelope while preserving terminal replay."""

        await self._scope(workspace_id)
        delivery = await self._session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.workspace_id == workspace_id,
                WebhookDelivery.id == delivery_id,
            )
            .with_for_update()
        )
        if delivery is None:
            raise AppError(
                "WEBHOOK_DELIVERY_NOT_FOUND",
                "Webhook 전달 작업을 찾을 수 없습니다.",
                404,
            )
        if delivery.state in {
            WebhookDeliveryState.DELIVERED.value,
            WebhookDeliveryState.DEAD_LETTERED.value,
            WebhookDeliveryState.CANCELLED.value,
        }:
            return delivery
        now = datetime.now(UTC)
        delivery.state = WebhookDeliveryState.DEAD_LETTERED.value
        delivery.dead_lettered_at = now
        delivery.last_error_code = error_code[:120] or "WEBHOOK_DELIVERY_FAILED"
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="webhook_delivery",
            aggregate_id=str(delivery.id),
            event_type="developer.webhook_delivery.dead_lettered",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "webhook_delivery_id": str(delivery.id),
                "webhook_event_id": str(delivery.event_id),
                "error_code": delivery.last_error_code,
            },
        )
        await self._session.flush()
        return delivery

    async def replay_delivery(
        self, principal: Principal, delivery_id: UUID, *, reason: str
    ) -> WebhookDelivery:
        await self._scope(principal.workspace_id)
        delivery = await self._session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.workspace_id == principal.workspace_id,
                WebhookDelivery.id == delivery_id,
            )
            .with_for_update()
        )
        if delivery is None:
            raise AppError("WEBHOOK_DELIVERY_NOT_FOUND", "Webhook 전달 작업을 찾을 수 없습니다.", 404)
        if delivery.manual_replay_count >= delivery.manual_replay_limit:
            raise AppError("WEBHOOK_REPLAY_LIMIT_EXCEEDED", "Webhook 수동 재전송 한도를 초과했습니다.", 409)
        endpoint = await self._session.scalar(
            select(WebhookEndpoint).where(
                WebhookEndpoint.workspace_id == principal.workspace_id,
                WebhookEndpoint.id == delivery.endpoint_id,
            )
        )
        if endpoint is None or endpoint.state != WebhookEndpointState.ACTIVE.value:
            raise AppError("WEBHOOK_ENDPOINT_DISABLED", "비활성 Endpoint로 재전송할 수 없습니다.", 409)
        delivery.manual_replay_count += 1
        delivery.cycle_attempt_count = 0
        delivery.state = WebhookDeliveryState.PENDING.value
        delivery.next_attempt_at = datetime.now(UTC)
        delivery.delivered_at = None
        delivery.dead_lettered_at = None
        delivery.last_error_code = None
        await self._session.flush()
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type="webhook_delivery",
            aggregate_id=str(delivery.id),
            event_type="developer.webhook_delivery.queued",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "webhook_delivery_id": str(delivery.id),
                "next_attempt_at": delivery.next_attempt_at.isoformat(),
                "reason": "MANUAL_REPLAY",
            },
        )
        await self._record(
            principal=principal,
            action="developer.webhook_delivery.replayed",
            target_type="webhook_delivery",
            target_id=delivery.id,
            details={"reason": reason, "manual_replay_count": delivery.manual_replay_count},
        )
        return delivery

    async def list_webhooks(self, principal: Principal) -> list[WebhookEndpoint]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(WebhookEndpoint)
                .where(WebhookEndpoint.workspace_id == principal.workspace_id)
                .order_by(WebhookEndpoint.created_at.desc())
            )
        )
