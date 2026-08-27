"""Repurposing format coverage and policy-driven validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from blogops.core.errors import AppError
from blogops.core.serialization import canonical_json_hash
from blogops.domain.repurpose.enums import RepurposeKind


REPURPOSE_REQUIREMENTS: dict[RepurposeKind, str] = {
    RepurposeKind.INSTAGRAM_CAPTION: "REP-001",
    RepurposeKind.THREADS_X: "REP-002",
    RepurposeKind.LINKEDIN: "REP-003",
    RepurposeKind.FACEBOOK: "REP-004",
    RepurposeKind.NEWSLETTER: "REP-005",
    RepurposeKind.AD_COPY: "REP-006",
    RepurposeKind.SEARCH_AD_COPY: "REP-007",
    RepurposeKind.SHORT_VIDEO_SCRIPT: "REP-008",
    RepurposeKind.YOUTUBE_DESCRIPTION: "REP-009",
    RepurposeKind.CARD_NEWS_COPY: "REP-010",
    RepurposeKind.REVIEW_RESPONSE: "REP-011",
    RepurposeKind.FAQ: "REP-012",
    RepurposeKind.SUPPORT_SCRIPT: "REP-013",
    RepurposeKind.PRESS_RELEASE_SUMMARY: "REP-014",
}


def ensure_secret_free_config(config: Mapping[str, Any]) -> None:
    markers = {"password", "token", "api_key", "apikey", "secret", "credential"}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(marker in normalized for marker in markers):
                    raise AppError(
                        code="REPURPOSE_INLINE_CREDENTIAL_FORBIDDEN",
                        message="모델 자격 증명은 리퍼포징 요청에 포함할 수 없습니다.",
                        status_code=422,
                        fields=[
                            {
                                "path": f"model_config.{path}{key}",
                                "reason": "server-side only",
                            }
                        ],
                    )
                walk(child, f"{path}{key}.")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}{index}.")

    walk(config, "")


def validate_platform_policy(kind: RepurposeKind, policy: Mapping[str, Any]) -> None:
    required = {"policy_version", "source", "constraints"}
    missing = sorted(required.difference(policy))
    if missing or not isinstance(policy.get("constraints", {}), Mapping):
        raise AppError(
            code="PLATFORM_POLICY_INCOMPLETE",
            message="플랫폼 제약은 출처와 버전이 있는 정책으로 제공되어야 합니다.",
            status_code=422,
            fields=[{"path": item, "reason": "required"} for item in missing],
        )
    if kind not in REPURPOSE_REQUIREMENTS:
        raise AppError(
            code="REPURPOSE_KIND_UNSUPPORTED",
            message="지원하지 않는 리퍼포징 형식입니다.",
            status_code=422,
        )
    constraints = policy["constraints"]
    if constraints.get("enforce_character_limit") and "max_characters" not in constraints:
        raise AppError(
            code="PLATFORM_CHARACTER_LIMIT_MISSING",
            message="문자 수 제한을 적용하려면 정책이 최대 문자 수를 제공해야 합니다.",
            status_code=422,
        )


def validate_policy_bundle(
    *,
    disclosure_policy: Mapping[str, Any],
    safety_policy: Mapping[str, Any],
    pii_policy: Mapping[str, Any],
    approval_policy: Mapping[str, Any],
    model_policy: Mapping[str, Any],
) -> None:
    for name, policy in (
        ("disclosure", disclosure_policy),
        ("safety", safety_policy),
        ("pii", pii_policy),
        ("approval", approval_policy),
        ("model", model_policy),
    ):
        if not str(policy.get("policy_version", "")).strip():
            raise AppError(
                code="REPURPOSE_POLICY_VERSION_REQUIRED",
                message="공개·안전·개인정보·승인·모델 정책은 버전이 필요합니다.",
                status_code=422,
                fields=[{"path": f"{name}_policy.policy_version", "reason": "required"}],
            )
    allowed_models = model_policy.get("allowed_models")
    if not isinstance(allowed_models, list) or not allowed_models:
        raise AppError(
            code="REPURPOSE_MODEL_POLICY_INCOMPLETE",
            message="모델 정책은 승인된 공급자·모델·버전 목록을 제공해야 합니다.",
            status_code=422,
        )


def validate_model_selection(
    policy: Mapping[str, Any], *, provider: str, model: str, model_version: str
) -> None:
    expected = {
        "provider": provider,
        "model": model,
        "model_version": model_version,
    }
    allowed = policy.get("allowed_models", [])
    if not any(
        isinstance(item, Mapping)
        and all(str(item.get(key, "")) == value for key, value in expected.items())
        for item in allowed
    ):
        raise AppError(
            code="REPURPOSE_MODEL_NOT_ALLOWED",
            message="템플릿 정책에 고정된 모델 공급자·모델·버전만 사용할 수 있습니다.",
            status_code=422,
        )


@dataclass(frozen=True)
class VariantValidation:
    passed: bool
    violations: tuple[dict[str, Any], ...]


def validate_variant(
    *,
    text: str,
    document: Sequence[Mapping[str, Any]],
    platform_policy: Mapping[str, Any],
    disclosure_result: Mapping[str, Any],
    safety_result: Mapping[str, Any],
    pii_result: Mapping[str, Any],
) -> VariantValidation:
    constraints = platform_policy.get("constraints", {})
    violations: list[dict[str, Any]] = []
    maximum = constraints.get("max_characters")
    minimum = constraints.get("min_characters")
    if maximum is not None and len(text) > _policy_integer(maximum, "max_characters"):
        violations.append({"code": "MAX_CHARACTERS", "actual": len(text), "limit": maximum})
    if minimum is not None and len(text) < _policy_integer(minimum, "min_characters"):
        violations.append({"code": "MIN_CHARACTERS", "actual": len(text), "limit": minimum})
    for phrase in constraints.get("required_phrases", []):
        if str(phrase) not in text:
            violations.append({"code": "REQUIRED_PHRASE", "value": str(phrase)})
    block_types = {str(block.get("type", "")) for block in document}
    for section in constraints.get("required_sections", []):
        if str(section) not in block_types:
            violations.append({"code": "REQUIRED_SECTION", "value": str(section)})
    for name, result in (
        ("DISCLOSURE", disclosure_result),
        ("SAFETY", safety_result),
        ("PII", pii_result),
    ):
        if result.get("passed") is not True:
            violations.append({"code": f"{name}_BLOCKED"})
    return VariantValidation(passed=not violations, violations=tuple(violations))


def validate_claim_lineage(
    output_lineage: Sequence[Mapping[str, Any]], allowed_claims: Mapping[str, str]
) -> None:
    for item in output_lineage:
        claim_id = str(item.get("claim_id", ""))
        claim_hash = str(item.get("claim_hash", ""))
        if not claim_id or allowed_claims.get(claim_id) != claim_hash:
            raise AppError(
                code="UNSUPPORTED_REPURPOSE_CLAIM",
                message="원문 스냅샷에 없는 주장이나 변경된 주장 해시는 사용할 수 없습니다.",
                status_code=422,
            )


def validate_citation_lineage(
    output_lineage: Sequence[Mapping[str, Any]], allowed_citations: Mapping[str, str]
) -> None:
    for item in output_lineage:
        citation_id = str(item.get("citation_id", ""))
        evidence_hash = str(item.get("evidence_hash", ""))
        if not citation_id or allowed_citations.get(citation_id) != evidence_hash:
            raise AppError(
                code="UNSUPPORTED_REPURPOSE_CITATION",
                message="원문 스냅샷에 없는 인용이나 변경된 증거 해시는 사용할 수 없습니다.",
                status_code=422,
            )


def require_passed_validation(validation: VariantValidation) -> None:
    if not validation.passed:
        raise AppError(
            code="REPURPOSE_POLICY_BLOCKED",
            message="플랫폼·공개·안전·개인정보 정책 검증을 통과하지 못했습니다.",
            status_code=422,
            fields=[{"path": "variant", "reason": str(item)} for item in validation.violations],
        )


def _policy_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AppError(
            code="PLATFORM_POLICY_INVALID",
            message="플랫폼 제약의 문자 수 값이 올바르지 않습니다.",
            status_code=422,
            fields=[{"path": f"constraints.{field}", "reason": "nonnegative integer"}],
        )
    return value
