"""Exact content/approval/quality/media readiness boundary for publishing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.publishing.rules import canonical_hash


@dataclass(frozen=True, slots=True)
class ReadyMedia:
    asset_id: UUID
    media_version_id: UUID
    placement_key: str
    object_ref: str
    content_hash: str
    mime_type: str
    filename: str
    alt_text: str
    caption: str | None
    attribution_text: str | None
    rights_snapshot_hash: str


@dataclass(frozen=True, slots=True)
class PublishReadyContent:
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    title: str
    document: list[dict[str, Any]]
    plain_text: str
    channel: str
    language: str
    tags: list[str]
    approval_request_id: UUID
    approval_snapshot_hash: str
    assessment_id: UUID
    assessment_hash: str
    quality_config_hash: str
    approved_by: UUID
    approved_at: datetime
    media: tuple[ReadyMedia, ...]


class PublishingReadinessResolver(Protocol):
    async def resolve(
        self,
        *,
        workspace_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        content_hash: str,
        approval_request_id: UUID,
        channel: str,
        require_media_license: bool,
    ) -> PublishReadyContent: ...


class PublishingEntitlementResolver(Protocol):
    async def max_connections(self, *, workspace_id: UUID) -> int: ...


class FailClosedPublishingEntitlementResolver:
    async def max_connections(self, *, workspace_id: UUID) -> int:
        del workspace_id
        raise AppError(
            "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
            "활성 게시 연결 한도가 포함된 유효한 Entitlement Snapshot이 필요합니다.",
            409,
        )


class SQLAlchemyPublishingReadinessResolver:
    """Reads stable contracts and fails closed on any stale approval or media right."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        workspace_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        content_hash: str,
        approval_request_id: UUID,
        channel: str,
        require_media_license: bool,
    ) -> PublishReadyContent:
        result = await self._session.execute(
            text(
                """
                SELECT c.id AS content_id, c.current_version_id, c.channel, c.language,
                       c.tags, v.id AS content_version_id, v.title, v.document,
                       v.plain_text, v.content_hash,
                       a.id AS approval_request_id, a.assessment_id,
                       a.assessment_hash, a.quality_config_hash,
                       a.approval_stages_hash, a.approved_by, a.approved_at,
                       a.approved_content_version_id, a.approved_content_hash
                FROM contents AS c
                JOIN content_versions AS v
                  ON v.workspace_id = c.workspace_id AND v.content_id = c.id
                JOIN content_approval_requests AS a
                  ON a.workspace_id = c.workspace_id
                 AND a.content_id = c.id
                 AND a.content_version_id = v.id
                JOIN quality_assessments AS qa
                  ON qa.workspace_id = a.workspace_id
                 AND qa.id = a.assessment_id
                 AND qa.content_id = c.id
                 AND qa.content_version_id = v.id
                 AND qa.content_hash = v.content_hash
                 AND qa.assessment_hash = a.assessment_hash
                 AND qa.quality_config_id = a.quality_config_id
                 AND qa.quality_config_hash = a.quality_config_hash
                WHERE c.workspace_id = :workspace_id
                  AND c.id = :content_id
                  AND c.channel = :channel
                  AND c.current_version_id = :content_version_id
                  AND v.id = :content_version_id
                  AND v.content_hash = :content_hash
                  AND a.id = :approval_request_id
                  AND a.status = 'APPROVED'
                  AND a.invalidated_at IS NULL
                  AND a.approved_content_version_id = v.id
                  AND a.approved_content_hash = v.content_hash
                  AND a.approved_by IS NOT NULL
                  AND a.approved_at IS NOT NULL
                  AND c.deleted_at IS NULL
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "content_id": str(content_id),
                "content_version_id": str(content_version_id),
                "content_hash": content_hash,
                "approval_request_id": str(approval_request_id),
                "channel": channel,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise AppError(
                "PUBLISH_READINESS_FAILED",
                "현재 콘텐츠 버전·해시와 정확히 일치하는 유효한 승인이 필요합니다.",
                409,
            )
        media = await self._media(
            workspace_id=workspace_id,
            content_version_id=content_version_id,
            content_hash=content_hash,
            channel=channel,
            require_media_license=require_media_license,
        )
        approval_snapshot = {
            "approval_request_id": str(row["approval_request_id"]),
            "content_version_id": str(row["approved_content_version_id"]),
            "content_hash": row["approved_content_hash"],
            "assessment_id": str(row["assessment_id"]),
            "assessment_hash": row["assessment_hash"],
            "quality_config_hash": row["quality_config_hash"],
            "approval_stages_hash": row["approval_stages_hash"],
            "approved_by": str(row["approved_by"]),
            "approved_at": row["approved_at"].isoformat(),
        }
        return PublishReadyContent(
            content_id=UUID(str(row["content_id"])),
            content_version_id=UUID(str(row["content_version_id"])),
            content_hash=str(row["content_hash"]),
            title=str(row["title"]),
            document=list(row["document"] or []),
            plain_text=str(row["plain_text"]),
            channel=str(row["channel"]),
            language=str(row["language"]),
            tags=list(row["tags"] or []),
            approval_request_id=UUID(str(row["approval_request_id"])),
            approval_snapshot_hash=canonical_hash(approval_snapshot),
            assessment_id=UUID(str(row["assessment_id"])),
            assessment_hash=str(row["assessment_hash"]),
            quality_config_hash=str(row["quality_config_hash"]),
            approved_by=UUID(str(row["approved_by"])),
            approved_at=row["approved_at"],
            media=tuple(media),
        )

    async def _media(
        self,
        *,
        workspace_id: UUID,
        content_version_id: UUID,
        content_hash: str,
        channel: str,
        require_media_license: bool,
    ) -> list[ReadyMedia]:
        result = await self._session.execute(
            text(
                """
                SELECT mu.asset_id, mu.media_version_id, mu.placement_key,
                       mu.alt_text, mu.caption,
                       mu.attribution_text AS usage_attribution_text,
                       mu.rights_snapshot, mu.rights_snapshot_hash, mu.usage_mode,
                       ma.name, ma.state AS asset_state, ma.deleted_at,
                       mv.object_ref, mv.content_hash AS media_content_hash, mv.mime_type,
                       ml.state AS license_state, ml.current_revision_id,
                       ml.valid_until AS license_valid_until, ml.revoked_at,
                       lr.id AS license_revision_id, lr.state AS revision_state,
                       lr.license_type, lr.commercial_allowed, lr.editorial_allowed,
                       lr.allowed_channels, lr.allowed_regions,
                       lr.attribution_required,
                       lr.attribution_text AS license_attribution_text,
                       lr.valid_from, lr.valid_until,
                       lr.snapshot_hash
                FROM media_usages AS mu
                JOIN media_assets AS ma
                  ON ma.workspace_id = mu.workspace_id AND ma.id = mu.asset_id
                JOIN media_versions AS mv
                  ON mv.workspace_id = mu.workspace_id
                 AND mv.asset_id = mu.asset_id
                 AND mv.id = mu.media_version_id
                JOIN media_licenses AS ml
                  ON ml.workspace_id = mu.workspace_id AND ml.asset_id = mu.asset_id
                JOIN media_license_revisions AS lr
                  ON lr.workspace_id = mu.workspace_id
                 AND lr.asset_id = mu.asset_id
                 AND lr.id = mu.license_revision_id
                WHERE mu.workspace_id = :workspace_id
                  AND mu.content_version_id = :content_version_id
                  AND mu.content_hash = :content_hash
                  AND mu.channel = :channel
                ORDER BY mu.placement_key, mu.id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "content_version_id": str(content_version_id),
                "content_hash": content_hash,
                "channel": channel,
            },
        )
        now = datetime.now(UTC)
        media: list[ReadyMedia] = []
        failures: list[str] = []
        for row in result.mappings():
            if require_media_license:
                allowed_channels = set(row["allowed_channels"] or [])
                rights_snapshot = (
                    row["rights_snapshot"]
                    if isinstance(row["rights_snapshot"], dict)
                    else {}
                )
                usage_allowed = (
                    (row["usage_mode"] != "COMMERCIAL" or bool(row["commercial_allowed"]))
                    and (row["usage_mode"] != "EDITORIAL" or bool(row["editorial_allowed"]))
                )
                ready = (
                    row["asset_state"] == "READY"
                    and row["deleted_at"] is None
                    and row["license_state"] == "ACTIVE"
                    and row["revision_state"] == "ACTIVE"
                    and row["revoked_at"] is None
                    and str(row["current_revision_id"]) == str(row["license_revision_id"])
                    and _rights_snapshot_matches(row, rights_snapshot)
                    and (row["license_valid_until"] is None or row["license_valid_until"] >= now)
                    and (row["valid_from"] is None or row["valid_from"] <= now)
                    and (row["valid_until"] is None or row["valid_until"] >= now)
                    and (not allowed_channels or channel in allowed_channels)
                    and usage_allowed
                )
                if not ready:
                    failures.append(str(row["placement_key"]))
                    continue
            media.append(
                ReadyMedia(
                    asset_id=UUID(str(row["asset_id"])),
                    media_version_id=UUID(str(row["media_version_id"])),
                    placement_key=str(row["placement_key"]),
                    object_ref=str(row["object_ref"]),
                    content_hash=str(row["media_content_hash"]),
                    mime_type=str(row["mime_type"]),
                    filename=str(row["name"]),
                    alt_text=str(row["alt_text"]),
                    caption=str(row["caption"]) if row["caption"] is not None else None,
                    attribution_text=(
                        str(row["usage_attribution_text"])
                        if row["usage_attribution_text"] is not None
                        else None
                    ),
                    rights_snapshot_hash=str(row["rights_snapshot_hash"]),
                )
            )
        if failures:
            raise AppError(
                "PUBLISH_MEDIA_LICENSE_NOT_READY",
                "게시 미디어의 현재 라이선스 또는 사용 범위가 유효하지 않습니다.",
                409,
                fields=[{"path": "placement_key", "reason": item} for item in failures],
            )
        return media


def _rights_snapshot_matches(
    row: Mapping[str, Any], snapshot: dict[str, Any]
) -> bool:
    expected_scalars = {
        "license_revision_id": str(row["license_revision_id"]),
        "license_type": row["license_type"],
        "state": row["revision_state"],
        "commercial_allowed": bool(row["commercial_allowed"]),
        "editorial_allowed": bool(row["editorial_allowed"]),
        "allowed_channels": row["allowed_channels"] or [],
        "allowed_regions": row["allowed_regions"] or [],
        "attribution_required": bool(row["attribution_required"]),
        "attribution_text": row["license_attribution_text"],
        "license_snapshot_hash": row["snapshot_hash"],
    }
    return bool(
        canonical_hash(snapshot) == str(row["rights_snapshot_hash"])
        and all(snapshot.get(key) == value for key, value in expected_scalars.items())
        and _same_instant(snapshot.get("valid_from"), row["valid_from"])
        and _same_instant(snapshot.get("valid_until"), row["valid_until"])
    )


def _same_instant(snapshot_value: Any, actual: datetime | None) -> bool:
    if actual is None:
        return snapshot_value is None
    if not isinstance(snapshot_value, str):
        return False
    try:
        parsed = datetime.fromisoformat(snapshot_value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    return parsed.astimezone(UTC) == actual.astimezone(UTC)
