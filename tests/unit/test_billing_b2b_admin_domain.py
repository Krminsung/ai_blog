from datetime import UTC, datetime, timedelta
from decimal import Decimal
from inspect import signature
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from blogops.core.errors import AppError
from blogops.domain.admin.enums import AdminCommandState
from blogops.domain.admin.models import AdminAction, AdminCommand
from blogops.domain.admin.providers import FailClosedAdminAdapters
from blogops.domain.admin.rules import (
    redact_admin_metadata,
    validate_notification_preference,
    validate_two_person_approval,
)
from blogops.domain.admin.schemas import NotificationPreferenceUpsert
from blogops.domain.admin.tasks import (
    process_admin_command_task,
    process_notification_delivery_task,
    schedule_due_notification_deliveries_task,
)
from blogops.domain.b2b.enums import ProvisioningState
from blogops.domain.b2b.models import ClientProvisioningRequest
from blogops.domain.b2b.providers import FailClosedB2BAdapters
from blogops.domain.b2b.rules import (
    authorize_portal_scopes,
    ensure_client_isolation,
    require_portal_target,
)
from blogops.domain.b2b.tasks import process_client_provisioning_task
from blogops.domain.billing.adapters import (
    BillingBulkBudgetAdapter,
    BillingMediaBudgetAdapter,
    DatabaseBudgetAuthorizationResolver,
    FailClosedBudgetAuthorizationResolver,
    create_budget_authorization_resolver,
    create_bulk_budget_gate,
    create_media_budget_gate,
)
from blogops.domain.billing.enums import BillingCycle, CreditHoldState, PaymentCommandState
from blogops.domain.billing.models import (
    CreditLedgerEntry,
    MoneyLedgerEntry,
    PaymentCommand,
    UsageRecord,
)
from blogops.domain.billing.providers import FailClosedPaymentGateway
from blogops.domain.billing.rules import (
    due_usage_thresholds,
    ensure_balance_transition,
    finalize_hold_amounts,
)
from blogops.domain.billing.schemas import CreditHoldFinalize, PaymentIntentCreate
from blogops.domain.billing.tasks import process_payment_intent_task
from blogops.domain.developer.models import ApiKey
from blogops.domain.developer.providers import FailClosedDeveloperAdapters
from blogops.domain.developer.security import (
    RateLimitRule,
    authorize_key_scopes,
    issue_api_key,
    required_rate_limit_rules,
    validate_webhook_destination,
    verify_api_key,
    verify_webhook_signature,
    webhook_replay_key,
    webhook_signature,
)
from blogops.domain.developer.tasks import process_webhook_delivery_task


def test_maximum_credit_hold_finalizes_actual_once_and_releases_difference() -> None:
    first = finalize_hold_amounts(
        state=CreditHoldState.HELD,
        maximum_amount=Decimal("30"),
        actual_amount=Decimal("23"),
    )
    assert first.consumed == Decimal("23")
    assert first.released == Decimal("7")
    assert first.replay is False

    replay = finalize_hold_amounts(
        state=CreditHoldState.FINALIZED,
        maximum_amount=Decimal("30"),
        actual_amount=Decimal("23"),
        finalized_amount=Decimal("23"),
    )
    assert replay.replay is True

    with pytest.raises(AppError) as conflict:
        finalize_hold_amounts(
            state=CreditHoldState.FINALIZED,
            maximum_amount=Decimal("30"),
            actual_amount=Decimal("24"),
            finalized_amount=Decimal("23"),
        )
    assert conflict.value.code == "CREDIT_HOLD_ALREADY_FINALIZED"

    with pytest.raises(AppError) as overrun:
        finalize_hold_amounts(
            state=CreditHoldState.HELD,
            maximum_amount=Decimal("30"),
            actual_amount=Decimal("31"),
        )
    assert overrun.value.code == "CREDIT_HOLD_MAXIMUM_EXCEEDED"


def test_failed_terminal_cost_requires_auditable_failure_metadata() -> None:
    value = CreditHoldFinalize(
        finalization_event_id="bulk-job:terminal:failed",
        actual_amount=Decimal("23"),
        failure_class="FINAL_FAILED",
        reason_code="BULK_TERMINAL_FINAL_FAILED",
    )
    assert value.failure_class == "FINAL_FAILED"

    with pytest.raises(ValidationError):
        CreditHoldFinalize(
            finalization_event_id="bulk-job:terminal:failed",
            actual_amount=Decimal("23"),
            failure_class="FINAL_FAILED",
        )


def test_payment_checkout_accepts_only_safe_absolute_return_urls() -> None:
    plan_version_id = uuid5(NAMESPACE_URL, "billing-plan")
    value = PaymentIntentCreate(
        operation="SUBSCRIBE",
        provider="approved-provider",
        plan_version_id=plan_version_id,
        billing_cycle=BillingCycle.MONTHLY,
        idempotency_key="payment-checkout-1",
        return_url="https://app.example.com/billing/complete",
    )
    assert value.return_url == "https://app.example.com/billing/complete"

    with pytest.raises(ValidationError):
        PaymentIntentCreate(
            operation="SUBSCRIBE",
            provider="approved-provider",
            plan_version_id=plan_version_id,
            billing_cycle=BillingCycle.MONTHLY,
            idempotency_key="payment-checkout-2",
            return_url="http://127.0.0.1/billing/complete",
        )


def test_money_credit_and_usage_are_separate_append_only_records() -> None:
    assert MoneyLedgerEntry.__tablename__ == "billing_money_ledger"
    assert CreditLedgerEntry.__tablename__ == "billing_credit_ledger"
    assert UsageRecord.__tablename__ == "billing_usage_records"
    assert len(
        {
            MoneyLedgerEntry.__tablename__,
            CreditLedgerEntry.__tablename__,
            UsageRecord.__tablename__,
        }
    ) == 3
    assert "available_delta" in CreditLedgerEntry.__table__.c
    assert "held_delta" in CreditLedgerEntry.__table__.c
    assert "reversal_of_id" in CreditLedgerEntry.__table__.c
    assert "reversal_of_id" in MoneyLedgerEntry.__table__.c

    ensure_balance_transition(
        available_before=Decimal("10"),
        held_before=Decimal("0"),
        available_after=Decimal("7"),
        held_after=Decimal("3"),
    )
    with pytest.raises(AppError) as negative:
        ensure_balance_transition(
            available_before=Decimal("1"),
            held_before=Decimal("0"),
            available_after=Decimal("-1"),
            held_after=Decimal("2"),
        )
    assert negative.value.code == "BILLING_AMOUNT_INVALID"


def test_usage_thresholds_are_explicit_and_newly_crossed_only() -> None:
    assert due_usage_thresholds(
        used_before=Decimal("69"),
        used_after=Decimal("100"),
        limit=Decimal("100"),
        thresholds=(70, 90, 100),
    ) == (70, 90, 100)
    with pytest.raises(AppError) as missing:
        due_usage_thresholds(
            used_before=Decimal("0"),
            used_after=Decimal("1"),
            limit=Decimal("10"),
            thresholds=(),
        )
    assert missing.value.code == "USAGE_THRESHOLD_CONFIG_MISSING"


def test_api_key_is_hashed_raw_is_not_persistable_and_scope_cannot_escalate() -> None:
    pepper = b"k" * 32
    material = issue_api_key(environment="production", pepper=pepper)
    assert material.raw.startswith("bops_live_")
    assert material.raw not in {column.name for column in ApiKey.__table__.columns}
    assert "raw_key" not in {column.name for column in ApiKey.__table__.columns}
    assert verify_api_key(material.raw, material.digest, pepper=pepper) is True
    assert verify_api_key(material.raw + "x", material.digest, pepper=pepper) is False

    scopes = authorize_key_scopes(
        requested={"content:read"},
        actor_permissions={"content:read", "content:write"},
        workspace_scopes={"content:read"},
    )
    assert scopes == frozenset({"content:read"})
    with pytest.raises(AppError) as escalation:
        authorize_key_scopes(
            requested={"content:read", "billing:manage"},
            actor_permissions={"content:read", "billing:manage"},
            workspace_scopes={"content:read"},
        )
    assert escalation.value.code == "API_KEY_SCOPE_ESCALATION"

    with pytest.raises(AppError) as unconfigured:
        issue_api_key(environment="production", pepper=b"short")
    assert unconfigured.value.code == "API_KEY_HASHER_UNAVAILABLE"


def test_workspace_endpoint_and_key_rate_limits_are_independently_enforced() -> None:
    workspace = RateLimitRule("workspace", 100, 60, 10)
    endpoint = RateLimitRule("endpoint", 20, 60, 2)
    key = RateLimitRule("key", 10, 60, 1)
    assert required_rate_limit_rules(
        workspace_rule=workspace,
        endpoint_rule=endpoint,
        key_rule=key,
    ) == (workspace, endpoint, key)
    with pytest.raises(AppError) as missing:
        required_rate_limit_rules(
            workspace_rule=workspace,
            endpoint_rule=None,
            key_rule=key,
        )
    assert missing.value.code == "API_RATE_LIMIT_CONFIG_MISSING"


def test_webhook_hmac_timestamp_replay_and_ssrf_boundaries() -> None:
    body = b'{"id":"evt_01","type":"job.completed"}'
    secret = b"w" * 32
    now = datetime.now(UTC)
    timestamp = int(now.timestamp())
    signature = webhook_signature(secret=secret, timestamp=timestamp, body=body)
    verify_webhook_signature(
        secret=secret,
        timestamp=timestamp,
        body=body,
        provided=signature,
        now=now,
        tolerance_seconds=300,
    )
    with pytest.raises(AppError) as expired:
        verify_webhook_signature(
            secret=secret,
            timestamp=timestamp - 301,
            body=body,
            provided=signature,
            now=now,
            tolerance_seconds=300,
        )
    assert expired.value.code == "WEBHOOK_TIMESTAMP_EXPIRED"

    endpoint_id = uuid5(NAMESPACE_URL, "webhook-endpoint")
    first = webhook_replay_key(
        endpoint_id=endpoint_id,
        event_id="evt_01",
        timestamp=timestamp,
        body=body,
    )
    second = webhook_replay_key(
        endpoint_id=endpoint_id,
        event_id="evt_01",
        timestamp=timestamp,
        body=body + b" ",
    )
    assert first != second
    assert (
        validate_webhook_destination(
            "https://hooks.example.com/events",
            resolved_addresses=["93.184.216.34"],
        )
        == "https://hooks.example.com/events"
    )
    with pytest.raises(AppError) as ssrf:
        validate_webhook_destination(
            "https://hooks.example.com/events",
            resolved_addresses=["127.0.0.1"],
        )
    assert ssrf.value.code == "SOURCE_URL_DNS_REBINDING_BLOCKED"
    with pytest.raises(AppError) as query_secret:
        validate_webhook_destination(
            "https://hooks.example.com/events?token=secret",
            resolved_addresses=["93.184.216.34"],
        )
    assert query_secret.value.code == "WEBHOOK_URL_SECRET_CHANNEL_BLOCKED"


def test_agency_client_portal_is_strictly_tenant_and_scope_bound() -> None:
    agency_workspace = uuid5(NAMESPACE_URL, "agency")
    client_a = uuid5(NAMESPACE_URL, "client-a")
    client_b = uuid5(NAMESPACE_URL, "client-b")
    ensure_client_isolation(
        agency_workspace_id=agency_workspace,
        client_workspace_id=client_a,
    )
    with pytest.raises(AppError):
        ensure_client_isolation(
            agency_workspace_id=agency_workspace,
            client_workspace_id=agency_workspace,
        )

    assert authorize_portal_scopes(
        requested={"content:read", "content:approve"},
        relationship_permissions={"content:read", "content:approve", "report:read"},
    ) == frozenset({"content:read", "content:approve"})
    with pytest.raises(AppError) as escalation:
        authorize_portal_scopes(
            requested={"content:read", "billing:manage"},
            relationship_permissions={"content:read", "billing:manage"},
        )
    assert escalation.value.code == "PORTAL_SCOPE_ESCALATION"

    with pytest.raises(AppError) as isolation:
        require_portal_target(
            grant_client_workspace_id=client_a,
            requested_workspace_id=client_b,
            grant_state="ACTIVE",
            expires_at=now_plus_hour(),
            now=datetime.now(UTC),
        )
    assert isolation.value.status_code == 404


def now_plus_hour() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def test_admin_metadata_is_masked_and_two_person_approval_is_enforced() -> None:
    masked = redact_admin_metadata(
        {
            "job_id": "job-1",
            "authorization": "Bearer secret",
            "nested": {"email": "user@example.com", "safe": "kept"},
            "client_secret": "must-hide",
        }
    )
    assert masked == {
        "job_id": "job-1",
        "authorization": "[REDACTED]",
        "nested": {"email": "[REDACTED]", "safe": "kept"},
        "client_secret": "[REDACTED]",
    }
    requester = uuid5(NAMESPACE_URL, "operator-requester")
    approver = uuid5(NAMESPACE_URL, "operator-approver")
    validate_two_person_approval(
        requested_by=requester,
        approver_id=approver,
        prior_approver_ids=(),
    )
    with pytest.raises(AppError) as self_approval:
        validate_two_person_approval(
            requested_by=requester,
            approver_id=requester,
            prior_approver_ids=(),
        )
    assert self_approval.value.code == "ADMIN_SEPARATION_OF_DUTIES"
    assert AdminAction.__tablename__ == "admin_actions"


def test_mandatory_security_notifications_cannot_be_disabled() -> None:
    with pytest.raises(AppError) as mandatory:
        validate_notification_preference(event_type="SECURITY", frequency="DISABLED")
    assert mandatory.value.code == "NOTIFICATION_MANDATORY"
    with pytest.raises(ValidationError):
        NotificationPreferenceUpsert(
            event_type="SECURITY",
            channel="EMAIL",
            frequency="DISABLED",
            timezone="Asia/Seoul",
        )


def test_external_operations_default_to_pending_not_synthetic_success() -> None:
    assert (
        PaymentCommand.__table__.c.state.default.arg
        == PaymentCommandState.PENDING_PROVIDER.value
    )
    assert (
        ClientProvisioningRequest.__table__.c.state.default.arg
        == ProvisioningState.QUEUED.value
    )
    assert AdminCommand.__table__.c.state.default.arg == AdminCommandState.PENDING_APPROVAL.value


def test_stage8_durable_worker_tasks_have_stable_names() -> None:
    assert process_payment_intent_task.name == "billing.payment_intent.process"
    assert process_webhook_delivery_task.name == "developer.webhook_delivery.process"
    assert process_client_provisioning_task.name == "b2b.client_provisioning.process"
    assert process_admin_command_task.name == "admin.command.process"
    assert process_notification_delivery_task.name == "admin.notification_delivery.process"
    assert (
        schedule_due_notification_deliveries_task.name
        == "admin.notification_deliveries.schedule_due"
    )


def test_stage6_budget_adapters_require_maximum_hold_and_terminal_event_ids() -> None:
    media_reserve = signature(BillingMediaBudgetAdapter.reserve).parameters
    bulk_reserve = signature(BillingBulkBudgetAdapter.reserve).parameters
    assert "maximum_cost" in media_reserve
    assert "maximum_cost" in bulk_reserve
    assert "terminal_event_id" in signature(BillingMediaBudgetAdapter.finalize).parameters
    assert "terminal_event_id" in signature(BillingMediaBudgetAdapter.release).parameters
    assert "terminal_event_id" in signature(BillingBulkBudgetAdapter.finalize).parameters
    assert "terminal_event_id" in signature(BillingBulkBudgetAdapter.release).parameters
    assert "actual_cost" in signature(BillingMediaBudgetAdapter.release).parameters
    assert "currency" in signature(BillingMediaBudgetAdapter.release).parameters
    assert "actual_cost" in signature(BillingBulkBudgetAdapter.release).parameters
    assert "currency" in signature(BillingBulkBudgetAdapter.release).parameters
    assert tuple(signature(create_budget_authorization_resolver).parameters) == ("session",)
    assert tuple(signature(create_media_budget_gate).parameters) == ("session",)
    assert tuple(signature(create_bulk_budget_gate).parameters) == ("session",)
    assert hasattr(DatabaseBudgetAuthorizationResolver, "authorize")


@pytest.mark.asyncio
async def test_unconfigured_external_adapters_fail_closed() -> None:
    payment = FailClosedPaymentGateway()
    with pytest.raises(AppError) as payment_error:
        await payment.verify_event(headers={}, body=b"{}")
    assert payment_error.value.code == "PAYMENT_WEBHOOK_VERIFIER_UNAVAILABLE"

    developer = FailClosedDeveloperAdapters()
    with pytest.raises(AppError) as dns_error:
        await developer.resolve_public("hooks.example.com")
    assert dns_error.value.code == "WEBHOOK_DNS_UNAVAILABLE"

    b2b = FailClosedB2BAdapters()
    with pytest.raises(AppError) as relationship_error:
        await b2b.authorize_client_relationship(
            agency_workspace_id=uuid5(NAMESPACE_URL, "agency"),
            client_workspace_id=uuid5(NAMESPACE_URL, "client"),
            permissions=frozenset({"content:read"}),
        )
    assert relationship_error.value.code == "AGENCY_CLIENT_AUTHORITY_UNAVAILABLE"

    admin = FailClosedAdminAdapters()
    with pytest.raises(AppError) as policy_error:
        await admin.required_approvals(command_kind="REFUND_REQUEST")
    assert policy_error.value.code == "ADMIN_APPROVAL_POLICY_UNAVAILABLE"

    budget = FailClosedBudgetAuthorizationResolver()
    with pytest.raises(AppError) as budget_error:
        await budget.authorize(
            workspace_id=uuid5(NAMESPACE_URL, "workspace"),
            operation_kind="bulk.generate",
            estimated_cost=Decimal("10"),
            requested_maximum_cost=Decimal("15"),
            currency="KRW",
        )
    assert budget_error.value.code == "BILLING_BUDGET_POLICY_UNAVAILABLE"
