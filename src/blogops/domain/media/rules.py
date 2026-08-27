"""Pure media security, privacy and usage-rights rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from blogops.core.errors import AppError
from blogops.core.serialization import canonical_json_hash as canonical_hash
from blogops.domain.media.enums import LicenseState, LicenseType, UsageMode

_SECRET_KEYS = frozenset(
    {
        "secret",
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "authorization",
        "auth_token",
        "bearer_token",
        "credential",
        "credentials",
        "client_key",
        "signing_key",
        "webhook_secret",
    }
)
_PRIVATE_METADATA_KEYS = frozenset(
    {
        "gps",
        "gps_info",
        "gps_latitude",
        "gps_longitude",
        "location",
        "serial_number",
        "camera_serial_number",
        "owner_name",
        "artist",
        "copyright_owner_email",
        "user_comment",
    }
)
_MIME_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}
_SAFE_OBJECT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{0,999}$")


def find_plaintext_secret_paths(value: Any, path: str = "config") -> list[str]:
    """Return credential-shaped paths without ever returning their values."""

    found: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            nested_path = f"{path}.{key}"
            normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            tokens = frozenset(normalized.split("_"))
            credential_shaped = (
                normalized in _SECRET_KEYS
                or normalized.endswith(("_secret", "_password", "_token", "_credential"))
                or ("private" in tokens and "key" in tokens)
                or ("api" in tokens and "key" in tokens)
            )
            if credential_shaped:
                found.append(nested_path)
            else:
                found.extend(find_plaintext_secret_paths(nested, nested_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(find_plaintext_secret_paths(nested, f"{path}[{index}]"))
    return found


def validate_private_object_ref(value: str, *, workspace_id: str, namespace: str) -> str:
    """Accept only opaque workspace-owned object keys, never public or file URLs."""

    expected_prefix = f"workspaces/{workspace_id}/{namespace}/"
    if not _SAFE_OBJECT_REF.fullmatch(value) or not value.startswith(expected_prefix):
        raise AppError(
            code="MEDIA_OBJECT_REF_INVALID",
            message="워크스페이스 전용 저장소 객체 참조가 올바르지 않습니다.",
            status_code=422,
            fields=[{"path": "object_ref", "reason": "workspace prefix mismatch"}],
        )
    return value


def validate_image_signature(content: bytes, declared_mime: str) -> None:
    """Reject extension-only uploads and unsupported active image formats."""

    signatures = _MIME_SIGNATURES.get(declared_mime.casefold())
    if signatures is None:
        raise AppError(
            code="MEDIA_TYPE_UNSUPPORTED",
            message="지원하지 않는 이미지 형식입니다.",
            status_code=422,
        )
    if declared_mime.casefold() == "image/webp":
        valid = content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    else:
        valid = any(content.startswith(signature) for signature in signatures)
    if not valid:
        raise AppError(
            code="MEDIA_SIGNATURE_MISMATCH",
            message="선언된 이미지 형식과 파일 내용이 일치하지 않습니다.",
            status_code=422,
        )


def sanitize_metadata(metadata: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Recursively remove location and device/person identifiers by default."""

    removed: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, nested in value.items():
                key = str(raw_key)
                nested_path = f"{path}.{key}" if path else key
                normalized = key.casefold().replace(" ", "_")
                if normalized in _PRIVATE_METADATA_KEYS or normalized.startswith("gps_"):
                    removed.append(nested_path)
                    continue
                result[key] = walk(nested, nested_path)
            return result
        if isinstance(value, (list, tuple)):
            return [walk(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    return walk(metadata, ""), removed


@dataclass(frozen=True, slots=True)
class RightsSnapshot:
    state: str
    license_type: str
    commercial_allowed: bool
    editorial_allowed: bool
    allowed_channels: tuple[str, ...]
    allowed_regions: tuple[str, ...]
    valid_from: datetime | None
    valid_until: datetime | None
    attribution_required: bool
    attribution_text: str | None
    terms_hash: str


@dataclass(frozen=True, slots=True)
class RightsDecision:
    allowed: bool
    reasons: tuple[str, ...]
    required_attribution: str | None


def evaluate_usage_rights(
    rights: RightsSnapshot,
    *,
    channel: str,
    region: str | None,
    usage_mode: UsageMode,
    used_at: datetime | None = None,
) -> RightsDecision:
    """Fail closed unless the pinned license revision permits the exact usage."""

    when = used_at or datetime.now(UTC)
    if when.tzinfo is None:
        raise AppError(
            code="MEDIA_USAGE_TIMEZONE_REQUIRED",
            message="사용 시각에는 시간대가 포함되어야 합니다.",
            status_code=422,
        )
    reasons: list[str] = []
    if rights.state != LicenseState.ACTIVE.value:
        reasons.append("license_not_active")
    if rights.valid_from is not None and when < rights.valid_from:
        reasons.append("license_not_started")
    if rights.valid_until is not None and when >= rights.valid_until:
        reasons.append("license_expired")
    if usage_mode == UsageMode.COMMERCIAL and not rights.commercial_allowed:
        reasons.append("commercial_use_not_allowed")
    if usage_mode == UsageMode.EDITORIAL and not rights.editorial_allowed:
        reasons.append("editorial_use_not_allowed")
    if rights.allowed_channels and channel not in rights.allowed_channels:
        reasons.append("channel_not_allowed")
    if rights.allowed_regions:
        if region is None:
            reasons.append("region_required")
        elif region not in rights.allowed_regions:
            reasons.append("region_not_allowed")
    if rights.attribution_required and not rights.attribution_text:
        reasons.append("attribution_missing")
    return RightsDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
        required_attribution=(rights.attribution_text if rights.attribution_required else None),
    )


def validate_license_fields(
    *,
    license_type: LicenseType,
    source_url: str | None,
    author: str | None,
    prompt_hash: str | None,
    model_name: str | None,
    commercial_allowed: bool,
    editorial_allowed: bool,
) -> None:
    if not commercial_allowed and not editorial_allowed:
        raise AppError(
            code="MEDIA_LICENSE_SCOPE_EMPTY",
            message="라이선스에는 하나 이상의 허용 사용 범위가 필요합니다.",
            status_code=422,
        )
    if license_type in {LicenseType.STOCK, LicenseType.THIRD_PARTY} and not source_url:
        raise AppError(
            code="MEDIA_LICENSE_SOURCE_REQUIRED",
            message="스톡 또는 제3자 이미지에는 출처 URL이 필요합니다.",
            status_code=422,
        )
    if license_type == LicenseType.THIRD_PARTY and not author:
        raise AppError(
            code="MEDIA_LICENSE_AUTHOR_REQUIRED",
            message="제3자 이미지에는 작성자 또는 권리자 정보가 필요합니다.",
            status_code=422,
        )
    if license_type == LicenseType.AI_GENERATED and (not prompt_hash or not model_name):
        raise AppError(
            code="MEDIA_AI_PROVENANCE_REQUIRED",
            message="AI 생성 이미지에는 모델과 프롬프트 계보가 필요합니다.",
            status_code=422,
        )


def ensure_real_photo_policy(*, requires_real_photo: bool, asset_ai_generated: bool) -> None:
    if requires_real_photo and asset_ai_generated:
        raise AppError(
            code="MEDIA_REAL_PHOTO_REQUIRED",
            message="실제 사용·방문 증빙 위치에는 AI 생성 이미지를 선택할 수 없습니다.",
            status_code=409,
        )
