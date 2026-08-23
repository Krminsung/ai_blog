import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from blogops.core.errors import AppError
from blogops.domain.bulk.parsing import preview_csv
from blogops.domain.bulk.providers import FailClosedTabularInputAdapter
from blogops.domain.bulk.rules import (
    callback_signature,
    can_retry,
    evaluate_budget_boundary,
    evaluate_spam_gate,
    input_snapshot_changed,
    validate_row_capacity,
    verify_callback_signature,
)
from blogops.domain.bulk.schemas import BulkInputRegister, BulkRowsIngest
from blogops.domain.jobs.state import JobState
from blogops.domain.media.enums import (
    LicenseState,
    LicenseType,
    MediaOperation,
    UsageMode,
)
from blogops.domain.media.models import MediaOperationJob
from blogops.domain.media.providers import (
    FailClosedMediaInspector,
    FailClosedMediaProvider,
    MediaProviderRequest,
)
from blogops.domain.media.rules import (
    RightsSnapshot,
    evaluate_usage_rights,
    sanitize_metadata,
    validate_image_signature,
)
from blogops.domain.media.schemas import (
    ImagePlanItemCreate,
    MediaLicenseRevisionCreate,
    MediaOperationCreate,
    MediaProviderConnectionCreate,
)


def test_image_signature_and_private_exif_metadata_are_checked() -> None:
    validate_image_signature(b"\x89PNG\r\n\x1a\nrest", "image/png")
    validate_image_signature(b"RIFF\x00\x00\x00\x00WEBPrest", "image/webp")
    with pytest.raises(AppError) as mismatch:
        validate_image_signature(b"not-an-image", "image/png")
    assert mismatch.value.code == "MEDIA_SIGNATURE_MISMATCH"

    sanitized, removed = sanitize_metadata(
        {
            "Camera": {"Model": "safe", "Serial Number": "private"},
            "GPS": {"latitude": 37.5, "longitude": 127.0},
            "description": "kept",
        }
    )
    assert sanitized == {"Camera": {"Model": "safe"}, "description": "kept"}
    assert set(removed) == {"Camera.Serial Number", "GPS"}


def test_license_revision_requires_provenance_and_exact_usage_scope() -> None:
    with pytest.raises((ValidationError, AppError)):
        MediaLicenseRevisionCreate(
            license_type=LicenseType.AI_GENERATED,
            commercial_allowed=True,
            prompt_hash=None,
            model_name=None,
        )

    now = datetime.now(UTC)
    rights = RightsSnapshot(
        state=LicenseState.ACTIVE.value,
        license_type=LicenseType.STOCK.value,
        commercial_allowed=True,
        editorial_allowed=True,
        allowed_channels=("WORDPRESS",),
        allowed_regions=("KR",),
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
        attribution_required=True,
        attribution_text="Photo: Example",
        terms_hash="a" * 64,
    )
    allowed = evaluate_usage_rights(
        rights,
        channel="WORDPRESS",
        region="KR",
        usage_mode=UsageMode.COMMERCIAL,
        used_at=now,
    )
    assert allowed.allowed is True
    assert allowed.required_attribution == "Photo: Example"
    blocked = evaluate_usage_rights(
        rights,
        channel="NAVER_MANUAL_PACKAGE",
        region="KR",
        usage_mode=UsageMode.COMMERCIAL,
        used_at=now,
    )
    assert blocked.allowed is False
    assert "channel_not_allowed" in blocked.reasons


def test_media_schemas_reject_secrets_and_false_real_photo_substitution() -> None:
    with pytest.raises(ValidationError):
        MediaProviderConnectionCreate(
            provider="image-provider",
            secret_ref="secret-manager://media/provider",
            capabilities={MediaOperation.TEXT_TO_IMAGE},
            config={"nested": {"api_key": "must-not-be-here"}},
        )

    with pytest.raises(ValidationError):
        ImagePlanItemCreate(
            need_kind="REAL_PHOTO_EVIDENCE",
            reason="실제 방문 증빙",
            requires_real_photo=True,
            generation_allowed=True,
            generation_prompt="가상의 방문 사진",
            alt_text_plan="방문 사진",
            placement={"after_block": "intro"},
        )

    with pytest.raises(ValidationError):
        MediaOperationCreate(
            operation=MediaOperation.TEXT_TO_IMAGE,
            provider_connection_id=uuid5(NAMESPACE_URL, "provider"),
            prompt="안전한 썸네일",
            policy_snapshot={"client_secret": "plaintext"},
            estimated_cost=Decimal("1"),
            maximum_cost=Decimal("2"),
            currency="KRW",
            idempotency_key="media-op-0001",
        )


@pytest.mark.asyncio
async def test_unconfigured_media_provider_and_inspector_fail_closed() -> None:
    request = MediaProviderRequest(
        workspace_id=uuid5(NAMESPACE_URL, "workspace"),
        job_id=uuid5(NAMESPACE_URL, "media-job"),
        operation=MediaOperation.TEXT_TO_IMAGE,
        input_snapshot={"prompt_hash": "a" * 64},
        input_snapshot_hash="b" * 64,
        source_object_refs=(),
        secret_ref="secret-manager://media/provider",
        budget_reservation_ref="hold://media/1",
    )
    with pytest.raises(AppError) as provider_error:
        await FailClosedMediaProvider().execute(request)
    assert provider_error.value.code == "MEDIA_PROVIDER_UNAVAILABLE"

    with pytest.raises(AppError) as inspector_error:
        await FailClosedMediaInspector().inspect(
            b"\x89PNG\r\n\x1a\n",
            declared_mime_type="image/png",
            policy_snapshot={},
        )
    assert inspector_error.value.code == "MEDIA_INSPECTOR_UNAVAILABLE"


def test_media_parent_job_uses_authoritative_job_state() -> None:
    assert MediaOperationJob.__table__.c.state.default.arg == JobState.CREATED.value


def test_csv_preview_handles_excel_encoding_masks_pii_and_has_no_thousand_row_cap() -> None:
    lines = ["keyword,title"]
    lines.extend(f"keyword-{index},title-{index}" for index in range(1_001))
    lines.append("contact test@example.com,private")
    preview = preview_csv(
        "\n".join(lines).encode("utf-8-sig"),
        column_mapping={"keyword": "keyword", "title": "title"},
        required_variables=("keyword", "title"),
        preview_limit=1_002,
    )
    assert preview.total_rows == 1_002
    assert preview.preview_rows[-1].mapped_values["keyword"] == "contact [EMAIL]"

    cp949_preview = preview_csv(
        "키워드,제목\n테스트,엑셀 저장".encode("cp949"),
        column_mapping={"키워드": "keyword", "제목": "title"},
        required_variables=("keyword", "title"),
        preview_limit=10,
    )
    assert cp949_preview.encoding == "cp949"
    assert cp949_preview.total_rows == 1


def test_bulk_scale_target_is_policy_driven_not_a_hard_product_maximum() -> None:
    validate_row_capacity(1_000, entitled_limit=None)
    validate_row_capacity(1_000_000, entitled_limit=None)
    with pytest.raises(AppError) as limited:
        validate_row_capacity(10_001, entitled_limit=10_000)
    assert limited.value.code == "BULK_ROW_ENTITLEMENT_EXCEEDED"

    rows = [
        {
            "row_no": index,
            "values": {"keyword": f"keyword-{index}"},
            "idempotency_key": f"bulk-row-{index:04d}",
            "estimated_cost": "0.1",
        }
        for index in range(1, 1_001)
    ]
    chunk = BulkRowsIngest.model_validate({"rows": rows})
    assert len(chunk.rows) == 1_000


def test_bulk_input_accepts_large_snapshot_count_and_keeps_secret_as_reference() -> None:
    workspace_id = uuid5(NAMESPACE_URL, "workspace")
    value = BulkInputRegister(
        input_kind="GOOGLE_SHEETS",
        name="million-row target snapshot",
        object_ref=f"workspaces/{workspace_id}/bulk/snapshot/rows.ndjson",
        content_hash="a" * 64,
        size_bytes=10,
        row_count=1_000_000,
        headers=["keyword"],
        source_locator="spreadsheet-id#Sheet1!A:Z",
        source_secret_ref="secret-manager://google/sheets/customer",
        malware_scan_status="NOT_REQUIRED",
    )
    assert value.row_count == 1_000_000
    assert "secret-manager://" in value.source_secret_ref


def test_budget_kill_switch_retry_and_signed_callback_rules() -> None:
    decision = evaluate_budget_boundary(
        finalized_cost=Decimal("40"),
        held_cost=Decimal("10"),
        next_estimated_cost=Decimal("6"),
        maximum_cost=Decimal("55"),
    )
    assert decision.allowed is False
    assert decision.kill_switch is True
    assert can_retry(attempt=2, max_attempts=3, retryable_error=True) is True
    assert can_retry(attempt=3, max_attempts=3, retryable_error=True) is False
    spam = evaluate_spam_gate(
        similarity_score=Decimal("0.95"),
        value_score=Decimal("20"),
        maximum_similarity=Decimal("0.90"),
        minimum_value=Decimal("50"),
    )
    assert spam.auto_publish_allowed is False
    assert set(spam.reasons) == {
        "similarity_threshold_exceeded",
        "value_threshold_not_met",
    }
    assert input_snapshot_changed(None, "a" * 64) is True
    assert input_snapshot_changed("a" * 64, "a" * 64) is False

    secret = b"callback-secret"
    payload = json.dumps({"row_id": "row-1"}, separators=(",", ":")).encode()
    now = datetime.now(UTC)
    timestamp = int(now.timestamp())
    signature = callback_signature(secret, timestamp=timestamp, payload=payload)
    verify_callback_signature(
        secret,
        timestamp=timestamp,
        payload=payload,
        supplied_signature=signature,
        now=now,
        tolerance_seconds=300,
    )
    with pytest.raises(AppError) as invalid:
        verify_callback_signature(
            secret,
            timestamp=timestamp,
            payload=payload,
            supplied_signature="0" * 64,
            now=now,
            tolerance_seconds=300,
        )
    assert invalid.value.code == "CALLBACK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_xlsx_and_sheet_adapter_is_fail_closed_when_unconfigured() -> None:
    iterator = FailClosedTabularInputAdapter().rows(
        object_ref="workspaces/test/bulk/input.xlsx",
        sheet="Sheet1",
        header_row=1,
        secret_ref=None,
    )
    with pytest.raises(AppError) as error:
        await anext(iterator)
    assert error.value.code == "BULK_INPUT_ADAPTER_UNAVAILABLE"
