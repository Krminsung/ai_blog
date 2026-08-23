"""Application services for private uploads, rights and non-destructive media jobs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.generation.models import ContentItem, ContentVersion
from blogops.domain.identity.models import Workspace
from blogops.domain.jobs.state import TERMINAL_JOB_STATES, JobState, ensure_job_transition
from blogops.domain.media.enums import (
    ImageSelectionState,
    InspectionStatus,
    LicenseState,
    MalwareScanStatus,
    MediaAssetState,
    MediaOperation,
    MediaOrigin,
    MediaProviderState,
    MediaVersionKind,
    UsageMode,
)
from blogops.domain.media.models import (
    MediaAsset,
    MediaInspection,
    MediaJobCommand,
    MediaLicense,
    MediaLicenseRevision,
    MediaOperationJob,
    MediaPlanItem,
    MediaPlanVersion,
    MediaProviderConnection,
    MediaScanResult,
    MediaUsage,
    MediaVersion,
)
from blogops.domain.media.providers import (
    MediaBudgetGate,
    MediaInspector,
)
from blogops.domain.media.rules import (
    RightsSnapshot,
    canonical_hash,
    ensure_real_photo_policy,
    evaluate_usage_rights,
    sanitize_metadata,
    validate_image_signature,
)
from blogops.domain.media.schemas import (
    ImagePlanCreate,
    ImageSelection,
    MediaDeleteRequest,
    MediaJobCommandRequest,
    MediaLicenseRevisionCreate,
    MediaOperationCreate,
    MediaProviderConnectionCreate,
    MediaRestoreVersion,
    MediaSensitiveReview,
    MediaUploadComplete,
    MediaUploadInitiate,
    MediaUsageCreate,
)
from blogops.domain.media.storage import PrivateObjectStorage, PrivateUploadGrant
from blogops.domain.planning.references import SQLAlchemyPlanningReferenceResolver
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_OUTBOX_SCHEMA_VERSION = "1.0"


class MalwareResultLike(Protocol):
    status: Any
    signature: str | None


class MalwareScannerLike(Protocol):
    async def scan(self, content: bytes) -> MalwareResultLike: ...


class MediaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _asset(
        self, workspace_id: UUID, asset_id: UUID, *, for_update: bool = False
    ) -> MediaAsset:
        query = select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id == asset_id,
            MediaAsset.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        asset = await self._session.scalar(query)
        if asset is None:
            raise _not_found("MEDIA_ASSET", "미디어 자산")
        return asset

    async def _version(
        self, workspace_id: UUID, asset_id: UUID, version_id: UUID
    ) -> MediaVersion:
        value = await self._session.scalar(
            select(MediaVersion).where(
                MediaVersion.workspace_id == workspace_id,
                MediaVersion.asset_id == asset_id,
                MediaVersion.id == version_id,
            )
        )
        if value is None:
            raise _not_found("MEDIA_VERSION", "미디어 버전")
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

    async def register_provider(
        self, principal: Principal, data: MediaProviderConnectionCreate
    ) -> MediaProviderConnection:
        await self._scope(principal.workspace_id)
        connection = MediaProviderConnection(
            workspace_id=principal.workspace_id,
            provider=data.provider,
            name=data.name,
            secret_ref=data.secret_ref,
            license_ref=data.license_ref,
            capabilities=sorted(item.value for item in data.capabilities),
            allowed_regions=data.allowed_regions,
            config_json=data.config,
            daily_quota=data.daily_quota,
            quota_remaining=data.daily_quota,
            created_by=principal.subject_id,
        )
        self._session.add(connection)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="media.provider.registered",
            aggregate_type="media_provider_connection",
            aggregate_id=connection.id,
            details={"provider": connection.provider, "capabilities": connection.capabilities},
        )
        return connection

    async def list_providers(self, principal: Principal) -> list[MediaProviderConnection]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(MediaProviderConnection)
                .where(MediaProviderConnection.workspace_id == principal.workspace_id)
                .order_by(MediaProviderConnection.provider, MediaProviderConnection.name)
            )
        )

    async def initiate_upload(
        self,
        principal: Principal,
        data: MediaUploadInitiate,
        *,
        storage: PrivateObjectStorage,
        max_upload_bytes: int,
        quarantine_ttl: timedelta = timedelta(hours=24),
    ) -> tuple[MediaAsset, PrivateUploadGrant]:
        await self._scope(principal.workspace_id)
        if data.size_bytes > max_upload_bytes:
            raise AppError(
                code="MEDIA_FILE_TOO_LARGE",
                message="이미지 용량이 워크스페이스 업로드 한도를 초과했습니다.",
                status_code=422,
                fields=[{"path": "size_bytes", "reason": f"limit={max_upload_bytes}"}],
            )
        asset = MediaAsset(
            workspace_id=principal.workspace_id,
            name=data.name or data.filename,
            origin=MediaOrigin.USER_UPLOAD.value,
            declared_mime_type=data.mime_type,
            declared_size_bytes=data.size_bytes,
            folder_path=data.folder_path,
            tags=data.tags,
            metadata_json={
                "filename": data.filename,
                "exif_policy": data.exif_policy.value,
                "expected_content_hash": data.expected_content_hash,
                "ai_disclosure_text": data.ai_disclosure_text,
            },
            ai_generated=data.ai_generated,
            ai_disclosure_required=data.ai_generated,
            quarantine_expires_at=datetime.now(UTC) + quarantine_ttl,
            created_by=principal.subject_id,
        )
        self._session.add(asset)
        await self._session.flush()
        grant = await storage.initiate_upload(
            workspace_id=principal.workspace_id,
            namespace="media",
            owner_id=asset.id,
            filename=data.filename,
            content_type=data.mime_type,
        )
        asset.quarantine_object_ref = grant.object_ref
        await self._record(
            principal=principal,
            action="media.upload.initiated",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"mime_type": data.mime_type, "size_bytes": data.size_bytes},
        )
        return asset, grant

    async def complete_upload(
        self,
        principal: Principal,
        asset_id: UUID,
        data: MediaUploadComplete,
        *,
        storage: PrivateObjectStorage,
    ) -> MediaAsset:
        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.state != MediaAssetState.AWAITING_UPLOAD.value or not asset.quarantine_object_ref:
            raise AppError(
                code="MEDIA_UPLOAD_STATE_INVALID",
                message="완료할 수 있는 업로드가 아닙니다.",
                status_code=409,
            )
        if asset.quarantine_expires_at is None or asset.quarantine_expires_at <= datetime.now(UTC):
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "upload_expired"
            await self._record(
                principal=principal,
                action="media.upload.blocked",
                aggregate_type="media_asset",
                aggregate_id=asset.id,
                details={"reason": asset.review_reason},
            )
            return asset
        details = await storage.head(asset.quarantine_object_ref)
        actual_size = int(details.get("ContentLength", -1))
        actual_type = str(details.get("ContentType", "")).split(";", 1)[0].casefold()
        if actual_size != asset.declared_size_bytes or actual_type != asset.declared_mime_type:
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "declared_metadata_mismatch"
            await self._record(
                principal=principal,
                action="media.upload.blocked",
                aggregate_type="media_asset",
                aggregate_id=asset.id,
                details={"reason": asset.review_reason},
            )
            return asset
        expected = data.expected_content_hash or asset.metadata_json.get("expected_content_hash")
        asset.metadata_json = {**asset.metadata_json, "expected_content_hash": expected}
        asset.state = MediaAssetState.QUARANTINED.value
        await self._record(
            principal=principal,
            action="media.upload.quarantined",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"scan_required": True},
        )
        return asset

    async def process_quarantined_upload(
        self,
        principal: Principal,
        asset_id: UUID,
        *,
        storage: PrivateObjectStorage,
        scanner: MalwareScannerLike,
        inspector: MediaInspector,
        max_upload_bytes: int,
        scanner_name: str,
        scanner_version: str,
        inspection_policy: dict[str, Any],
    ) -> MediaAsset:
        """Worker entry point; no bytes are promoted until all checks succeed."""

        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.state not in {
            MediaAssetState.QUARANTINED.value,
            MediaAssetState.SCANNING.value,
            MediaAssetState.INSPECTING.value,
        } or not asset.quarantine_object_ref:
            raise AppError("MEDIA_SCAN_STATE_INVALID", "검사할 격리 이미지가 없습니다.", 409)
        if asset.quarantine_expires_at is None or asset.quarantine_expires_at <= datetime.now(UTC):
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "upload_expired"
            await self._record(
                principal=principal,
                action="media.upload.blocked",
                aggregate_type="media_asset",
                aggregate_id=asset.id,
                details={"reason": asset.review_reason},
            )
            return asset
        asset.state = MediaAssetState.SCANNING.value
        content = await storage.get_bytes(asset.quarantine_object_ref, max_bytes=max_upload_bytes)
        validate_image_signature(content, asset.declared_mime_type)
        content_hash = hashlib.sha256(content).hexdigest()
        expected_hash = asset.metadata_json.get("expected_content_hash")
        if expected_hash and expected_hash != content_hash:
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "content_hash_mismatch"
            await self._record(
                principal=principal,
                action="media.upload.blocked",
                aggregate_type="media_asset",
                aggregate_id=asset.id,
                details={"reason": asset.review_reason},
            )
            return asset

        scan_value = await scanner.scan(content)
        scan_status = getattr(scan_value.status, "value", str(scan_value.status))
        if scan_status not in {item.value for item in MalwareScanStatus}:
            scan_status = MalwareScanStatus.UNAVAILABLE.value
        scan = MediaScanResult(
            workspace_id=principal.workspace_id,
            asset_id=asset.id,
            quarantine_object_ref=asset.quarantine_object_ref,
            content_hash=content_hash,
            size_bytes=len(content),
            detected_mime_type=asset.declared_mime_type,
            scanner=scanner_name,
            scanner_version=scanner_version,
            status=scan_status,
            signature=scan_value.signature,
            details_json={},
            scanned_at=datetime.now(UTC),
        )
        self._session.add(scan)
        await self._session.flush()
        if scan_status != MalwareScanStatus.CLEAN.value:
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = (
                "malware_detected"
                if scan_status == MalwareScanStatus.INFECTED.value
                else "malware_scanner_unavailable"
            )
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
            await self._record(
                principal=principal,
                action="media.upload.blocked",
                aggregate_type="media_asset",
                aggregate_id=asset.id,
                details={"reason": asset.review_reason},
            )
            return asset

        asset.state = MediaAssetState.INSPECTING.value
        inspection_value = await inspector.inspect(
            content,
            declared_mime_type=asset.declared_mime_type,
            policy_snapshot={
                **inspection_policy,
                "exif_policy": asset.metadata_json.get("exif_policy"),
            },
        )
        if inspection_value.detected_mime_type.casefold() != asset.declared_mime_type:
            raise AppError(
                "MEDIA_INSPECTION_TYPE_MISMATCH",
                "검사기가 보고한 이미지 형식이 원본과 다릅니다.",
                422,
            )
        validate_image_signature(
            inspection_value.sanitized_content, inspection_value.detected_mime_type
        )
        sanitized_metadata, additionally_removed = sanitize_metadata(
            inspection_value.sanitized_metadata
        )
        if asset.metadata_json.get("exif_policy") == "REMOVE_ALL":
            additionally_removed = sorted(
                set(additionally_removed).union(inspection_value.sanitized_metadata)
            )
            sanitized_metadata = {}
        sanitized_hash = hashlib.sha256(inspection_value.sanitized_content).hexdigest()
        status_value = inspection_value.status
        if status_value not in {item.value for item in InspectionStatus}:
            status_value = InspectionStatus.UNAVAILABLE.value
        immutable_ref = None
        if status_value in {
            InspectionStatus.SAFE.value,
            InspectionStatus.NEEDS_REVIEW.value,
        }:
            immutable_ref = await storage.put_immutable(
                workspace_id=principal.workspace_id,
                namespace="media",
                owner_id=asset.id,
                content_hash=sanitized_hash,
                content=inspection_value.sanitized_content,
                content_type=inspection_value.detected_mime_type,
            )
        inspection = MediaInspection(
            workspace_id=principal.workspace_id,
            asset_id=asset.id,
            scan_result_id=scan.id,
            inspector=inspection_value.inspector,
            inspector_version=inspection_value.inspector_version,
            status=status_value,
            width=inspection_value.width,
            height=inspection_value.height,
            sanitized_metadata=sanitized_metadata,
            removed_metadata_paths=sorted(
                set(inspection_value.removed_metadata_paths).union(additionally_removed)
            ),
            pii_findings=list(inspection_value.pii_findings),
            face_findings=list(inspection_value.face_findings),
            trademark_findings=list(inspection_value.trademark_findings),
            safety_findings=list(inspection_value.safety_findings),
            transformation_log=list(inspection_value.transformation_log),
            sanitized_object_ref=immutable_ref,
            sanitized_content_hash=sanitized_hash,
            sanitized_size_bytes=len(inspection_value.sanitized_content),
            inspected_at=datetime.now(UTC),
        )
        self._session.add(inspection)
        await self._session.flush()
        needs_review = bool(
            inspection.pii_findings
            or inspection.face_findings
            or inspection.trademark_findings
            or status_value == InspectionStatus.NEEDS_REVIEW.value
        )
        if status_value in {InspectionStatus.BLOCKED.value, InspectionStatus.UNAVAILABLE.value}:
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "media_safety_blocked"
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
        elif needs_review:
            asset.state = MediaAssetState.NEEDS_REVIEW.value
            asset.review_reason = "sensitive_media_review_required"
        else:
            await self._promote_original(principal, asset, inspection)
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
        await self._record(
            principal=principal,
            action="media.upload.inspected",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"status": asset.state, "inspection_id": str(inspection.id)},
        )
        return asset

    async def block_quarantined_upload(
        self,
        principal: Principal,
        asset_id: UUID,
        *,
        error_code: str,
    ) -> MediaAsset:
        """Persist a fail-closed worker outcome without exposing exception details."""

        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.state in {
            MediaAssetState.READY.value,
            MediaAssetState.BLOCKED.value,
            MediaAssetState.DELETED.value,
        }:
            return asset
        asset.state = MediaAssetState.BLOCKED.value
        asset.review_reason = error_code.casefold()
        await self._record(
            principal=principal,
            action="media.upload.blocked",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"reason": asset.review_reason},
        )
        return asset

    async def _promote_original(
        self,
        principal: Principal,
        asset: MediaAsset,
        inspection: MediaInspection,
    ) -> MediaVersion:
        if inspection.status not in {
            InspectionStatus.SAFE.value,
            InspectionStatus.NEEDS_REVIEW.value,
        }:
            raise AppError("MEDIA_PROMOTION_BLOCKED", "안전 검사를 통과하지 못했습니다.", 409)
        if not inspection.sanitized_object_ref:
            raise AppError(
                "MEDIA_SANITIZED_OBJECT_MISSING",
                "검증된 비공개 이미지 객체가 없어 승격할 수 없습니다.",
                409,
            )
        duplicate_asset = await self._session.scalar(
            select(MediaAsset.id).where(
                MediaAsset.workspace_id == principal.workspace_id,
                MediaAsset.id != asset.id,
                MediaAsset.original_content_hash == inspection.sanitized_content_hash,
                MediaAsset.deleted_at.is_(None),
            )
        )
        if duplicate_asset is not None:
            asset.metadata_json = {
                **asset.metadata_json,
                "duplicate_of_asset_id": str(duplicate_asset),
            }
        existing = await self._session.scalar(
            select(MediaVersion).where(
                MediaVersion.workspace_id == principal.workspace_id,
                MediaVersion.asset_id == asset.id,
                MediaVersion.content_hash == inspection.sanitized_content_hash,
            )
        )
        if existing is None:
            existing = MediaVersion(
                workspace_id=principal.workspace_id,
                asset_id=asset.id,
                version_number=1,
                version_kind=MediaVersionKind.ORIGINAL.value,
                operation="UPLOAD_SANITIZE",
                object_ref=inspection.sanitized_object_ref,
                content_hash=inspection.sanitized_content_hash,
                mime_type=asset.declared_mime_type,
                size_bytes=inspection.sanitized_size_bytes,
                width=inspection.width,
                height=inspection.height,
                provenance_json={"inspection_id": str(inspection.id)},
                sanitized_metadata=inspection.sanitized_metadata,
                removed_metadata_paths=inspection.removed_metadata_paths,
                pii_detected=bool(inspection.pii_findings),
                face_detected=bool(inspection.face_findings),
                trademark_detected=bool(inspection.trademark_findings),
                safety_labels=inspection.safety_findings,
                ai_generated=asset.ai_generated,
                disclosure_text=asset.metadata_json.get("ai_disclosure_text"),
                created_by=principal.subject_id,
            )
            self._session.add(existing)
            await self._session.flush()
        asset.original_content_hash = existing.content_hash
        asset.original_version_id = existing.id
        asset.current_version_id = existing.id
        asset.state = MediaAssetState.READY.value
        asset.review_reason = None
        return existing

    async def review_sensitive_upload(
        self,
        principal: Principal,
        asset_id: UUID,
        data: MediaSensitiveReview,
        *,
        storage: PrivateObjectStorage,
    ) -> MediaAsset:
        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.state != MediaAssetState.NEEDS_REVIEW.value:
            raise AppError("MEDIA_REVIEW_STATE_INVALID", "검토 대기 이미지가 아닙니다.", 409)
        inspection = await self._session.scalar(
            select(MediaInspection)
            .where(
                MediaInspection.workspace_id == principal.workspace_id,
                MediaInspection.asset_id == asset.id,
            )
            .order_by(MediaInspection.inspected_at.desc())
            .limit(1)
        )
        if inspection is None:
            raise _not_found("MEDIA_INSPECTION", "미디어 검사 결과")
        if not data.approve:
            asset.state = MediaAssetState.BLOCKED.value
            asset.review_reason = "rejected_by_reviewer"
            if inspection.sanitized_object_ref:
                await storage.delete(inspection.sanitized_object_ref)
        else:
            if inspection.face_findings and not data.face_consent_confirmed:
                raise AppError(
                    "MEDIA_FACE_CONSENT_REQUIRED",
                    "얼굴이 탐지된 이미지에는 동의 확인이 필요합니다.",
                    422,
                )
            if inspection.pii_findings and not data.pii_removal_confirmed:
                raise AppError(
                    "MEDIA_PII_REVIEW_REQUIRED",
                    "개인정보 제거 결과 확인이 필요합니다.",
                    422,
                )
            await self._promote_original(principal, asset, inspection)
        if asset.quarantine_object_ref:
            await storage.delete(asset.quarantine_object_ref)
            asset.quarantine_object_ref = None
        await self._record(
            principal=principal,
            action="media.upload.reviewed",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"approved": data.approve, "reason": data.reason},
        )
        return asset

    async def list_assets(
        self,
        principal: Principal,
        *,
        state: str | None,
        folder_path: str | None,
        limit: int,
        offset: int,
    ) -> list[MediaAsset]:
        await self._scope(principal.workspace_id)
        query = select(MediaAsset).where(
            MediaAsset.workspace_id == principal.workspace_id,
            MediaAsset.deleted_at.is_(None),
        )
        if state:
            query = query.where(MediaAsset.state == state)
        if folder_path:
            query = query.where(MediaAsset.folder_path == folder_path)
        return list(
            await self._session.scalars(
                query.order_by(MediaAsset.created_at.desc()).limit(limit).offset(offset)
            )
        )

    async def get_asset(self, principal: Principal, asset_id: UUID) -> MediaAsset:
        await self._scope(principal.workspace_id)
        return await self._asset(principal.workspace_id, asset_id)

    async def list_versions(
        self, principal: Principal, asset_id: UUID
    ) -> list[MediaVersion]:
        await self._scope(principal.workspace_id)
        await self._asset(principal.workspace_id, asset_id)
        return list(
            await self._session.scalars(
                select(MediaVersion)
                .where(
                    MediaVersion.workspace_id == principal.workspace_id,
                    MediaVersion.asset_id == asset_id,
                )
                .order_by(MediaVersion.version_number)
            )
        )

    async def restore_version(
        self,
        principal: Principal,
        asset_id: UUID,
        data: MediaRestoreVersion,
    ) -> MediaAsset:
        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.lock_version != data.expected_lock_version:
            raise AppError("OPTIMISTIC_LOCK_CONFLICT", "미디어 자산이 변경되었습니다.", 409)
        await self._version(principal.workspace_id, asset_id, data.version_id)
        asset.current_version_id = data.version_id
        await self._record(
            principal=principal,
            action="media.version.restored",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"version_id": str(data.version_id)},
        )
        return asset

    async def add_license_revision(
        self,
        principal: Principal,
        asset_id: UUID,
        data: MediaLicenseRevisionCreate,
    ) -> tuple[MediaLicense, MediaLicenseRevision]:
        await self._scope(principal.workspace_id)
        await self._asset(principal.workspace_id, asset_id)
        ledger = await self._session.scalar(
            select(MediaLicense)
            .where(
                MediaLicense.workspace_id == principal.workspace_id,
                MediaLicense.asset_id == asset_id,
            )
            .with_for_update()
        )
        if ledger is None:
            ledger = MediaLicense(
                workspace_id=principal.workspace_id,
                asset_id=asset_id,
                created_by=principal.subject_id,
            )
            self._session.add(ledger)
            await self._session.flush()
        latest = await self._session.scalar(
            select(func.coalesce(func.max(MediaLicenseRevision.revision), 0)).where(
                MediaLicenseRevision.workspace_id == principal.workspace_id,
                MediaLicenseRevision.license_id == ledger.id,
            )
        )
        snapshot = data.model_dump(mode="json")
        revision = MediaLicenseRevision(
            workspace_id=principal.workspace_id,
            license_id=ledger.id,
            asset_id=asset_id,
            revision=int(latest or 0) + 1,
            state=data.state.value,
            license_type=data.license_type.value,
            source_url=data.source_url,
            source_asset_ref=data.source_asset_ref,
            author=data.author,
            downloaded_at=data.downloaded_at,
            commercial_allowed=data.commercial_allowed,
            editorial_allowed=data.editorial_allowed,
            allowed_channels=data.allowed_channels,
            allowed_regions=data.allowed_regions,
            derivative_allowed=data.derivative_allowed,
            attribution_required=data.attribution_required,
            attribution_text=data.attribution_text,
            valid_from=data.valid_from,
            valid_until=data.valid_until,
            terms_json=data.terms,
            evidence_object_ref=data.evidence_object_ref,
            model_name=data.model_name,
            model_version=data.model_version,
            prompt_hash=data.prompt_hash,
            confirmed_by=principal.subject_id,
            confirmed_at=datetime.now(UTC),
            snapshot_hash=canonical_hash(snapshot),
        )
        self._session.add(revision)
        await self._session.flush()
        ledger.current_revision_id = revision.id
        ledger.state = data.state.value
        ledger.valid_until = data.valid_until
        ledger.revoked_at = (
            datetime.now(UTC) if data.state == LicenseState.REVOKED else None
        )
        await self._record(
            principal=principal,
            action="media.license.revised",
            aggregate_type="media_license",
            aggregate_id=ledger.id,
            details={"asset_id": str(asset_id), "revision": revision.revision},
        )
        return ledger, revision

    async def current_license(
        self, principal: Principal, asset_id: UUID
    ) -> tuple[MediaLicense, MediaLicenseRevision]:
        await self._scope(principal.workspace_id)
        ledger = await self._session.scalar(
            select(MediaLicense).where(
                MediaLicense.workspace_id == principal.workspace_id,
                MediaLicense.asset_id == asset_id,
            )
        )
        if ledger is None or ledger.current_revision_id is None:
            raise _not_found("MEDIA_LICENSE", "미디어 라이선스")
        revision = await self._session.scalar(
            select(MediaLicenseRevision).where(
                MediaLicenseRevision.workspace_id == principal.workspace_id,
                MediaLicenseRevision.asset_id == asset_id,
                MediaLicenseRevision.id == ledger.current_revision_id,
            )
        )
        if revision is None:
            raise _not_found("MEDIA_LICENSE_REVISION", "미디어 라이선스 버전")
        return ledger, revision

    async def create_operation_job(
        self,
        principal: Principal,
        data: MediaOperationCreate,
        *,
        budget_gate: MediaBudgetGate,
    ) -> tuple[MediaOperationJob, bool]:
        await self._scope(principal.workspace_id)
        request_payload = data.model_dump(mode="json")
        request_hash = canonical_hash(request_payload)
        existing = await self._session.scalar(
            select(MediaOperationJob).where(
                MediaOperationJob.workspace_id == principal.workspace_id,
                MediaOperationJob.requested_by == principal.subject_id,
                MediaOperationJob.operation == data.operation.value,
                MediaOperationJob.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 멱등키가 다른 이미지 요청에 사용되었습니다.",
                    409,
                )
            return existing, False
        connection = await self._session.scalar(
            select(MediaProviderConnection)
            .where(
                MediaProviderConnection.workspace_id == principal.workspace_id,
                MediaProviderConnection.id == data.provider_connection_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise _not_found("MEDIA_PROVIDER", "이미지 공급자 연결")
        if connection.state != MediaProviderState.ACTIVE.value:
            raise AppError("MEDIA_PROVIDER_INACTIVE", "이미지 공급자 연결을 사용할 수 없습니다.", 409)
        now = datetime.now(UTC)
        if (
            connection.daily_quota is not None
            and connection.quota_reset_at is not None
            and connection.quota_reset_at <= now
        ):
            connection.quota_remaining = connection.daily_quota
            connection.quota_reset_at = now + timedelta(days=1)
        if connection.circuit_open_until is not None and connection.circuit_open_until > now:
            raise AppError("MEDIA_PROVIDER_CIRCUIT_OPEN", "이미지 공급자 회로가 열려 있습니다.", 503)
        if connection.quota_remaining is not None and connection.quota_remaining <= 0:
            raise AppError("MEDIA_PROVIDER_QUOTA_EXHAUSTED", "이미지 공급자 할당량을 소진했습니다.", 429)
        if data.operation.value not in connection.capabilities:
            raise AppError(
                "MEDIA_PROVIDER_CAPABILITY_UNAVAILABLE",
                "공급자 연결이 요청 이미지 작업을 지원하지 않습니다.",
                422,
            )
        if connection.allowed_regions and (
            data.region is None or data.region not in connection.allowed_regions
        ):
            raise AppError(
                "MEDIA_PROVIDER_REGION_BLOCKED",
                "이미지 공급자 연결이 요청 지역에서 사용을 허용하지 않습니다.",
                409,
            )
        if data.source_asset_id and data.source_version_id:
            source_asset = await self._asset(principal.workspace_id, data.source_asset_id)
            if source_asset.state != MediaAssetState.READY.value:
                raise AppError("MEDIA_SOURCE_NOT_READY", "원본 이미지가 준비되지 않았습니다.", 409)
            await self._version(
                principal.workspace_id, data.source_asset_id, data.source_version_id
            )
            _ledger, source_rights = await self.current_license(
                principal, data.source_asset_id
            )
            if (
                source_rights.state != LicenseState.ACTIVE.value
                or (
                    source_rights.valid_from is not None
                    and source_rights.valid_from > now
                )
                or (
                    source_rights.valid_until is not None
                    and source_rights.valid_until <= now
                )
            ):
                raise AppError(
                    "MEDIA_SOURCE_RIGHTS_INACTIVE",
                    "원본 이미지 라이선스가 활성 상태가 아닙니다.",
                    409,
                )
            if data.operation not in {
                MediaOperation.ALT_CAPTION,
                MediaOperation.STOCK_SEARCH,
            } and not source_rights.derivative_allowed:
                raise AppError(
                    "MEDIA_DERIVATIVE_NOT_ALLOWED",
                    "고정된 라이선스 버전이 파생 편집을 허용하지 않습니다.",
                    409,
                )
        for reference in data.reference_versions:
            reference_asset = await self._asset(
                principal.workspace_id, reference.asset_id
            )
            if reference_asset.state != MediaAssetState.READY.value:
                raise AppError(
                    "MEDIA_REFERENCE_NOT_READY",
                    "참조 이미지가 준비되지 않았습니다.",
                    409,
                )
            await self._version(
                principal.workspace_id,
                reference.asset_id,
                reference.version_id,
            )
            _ledger, reference_rights = await self.current_license(
                principal, reference.asset_id
            )
            if (
                reference_rights.state != LicenseState.ACTIVE.value
                or not reference_rights.derivative_allowed
                or (
                    reference_rights.valid_from is not None
                    and reference_rights.valid_from > now
                )
                or (
                    reference_rights.valid_until is not None
                    and reference_rights.valid_until <= now
                )
            ):
                raise AppError(
                    "MEDIA_REFERENCE_RIGHTS_BLOCKED",
                    "참조 이미지의 활성 라이선스가 파생 사용을 허용하지 않습니다.",
                    409,
                )
        provider_policy = connection.config_json.get("policy_snapshot")
        if not isinstance(provider_policy, dict):
            raise AppError(
                "MEDIA_PROVIDER_POLICY_MISSING",
                "서버에서 관리하는 이미지 공급자 정책을 확인할 수 없습니다.",
                503,
            )
        reservation = await budget_gate.reserve(
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            operation=data.operation,
            estimated_cost=data.estimated_cost,
            maximum_cost=data.maximum_cost,
            currency=data.currency.upper(),
            idempotency_key=data.idempotency_key,
        )
        if reservation.authorized_amount < data.maximum_cost:
            raise AppError(
                "MEDIA_BUDGET_EXCEEDED",
                "이미지 작업의 최대 비용 전액이 Hold되지 않았습니다.",
                402,
            )
        policy_snapshot = {
            "provider_policy": provider_policy,
            "budget_policy": dict(reservation.policy_snapshot),
            "allowed_regions": connection.allowed_regions,
        }
        input_snapshot = {
            "source_asset_id": str(data.source_asset_id) if data.source_asset_id else None,
            "source_version_id": str(data.source_version_id) if data.source_version_id else None,
            "reference_versions": [
                value.model_dump(mode="json") for value in data.reference_versions
            ],
            "parameters": data.parameters,
            "prohibited_elements": data.prohibited_elements,
            "region": data.region,
        }
        prompt_snapshot = (
            {"text": data.prompt, "prohibited_elements": data.prohibited_elements}
            if data.prompt
            else None
        )
        job = MediaOperationJob(
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            operation=data.operation.value,
            state=JobState.QUEUED.value,
            provider_connection_id=connection.id,
            source_asset_id=data.source_asset_id,
            source_version_id=data.source_version_id,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            input_snapshot=input_snapshot,
            input_snapshot_hash=canonical_hash(input_snapshot),
            policy_snapshot=policy_snapshot,
            policy_snapshot_hash=canonical_hash(policy_snapshot),
            prompt_snapshot=prompt_snapshot,
            prompt_hash=canonical_hash(prompt_snapshot) if prompt_snapshot else None,
            estimated_cost=data.estimated_cost,
            currency=data.currency.upper(),
            budget_reservation_ref=reservation.reservation_ref,
            budget_limit=data.maximum_cost,
            provider_quota_reserved=connection.quota_remaining is not None,
            max_attempts=data.max_attempts,
        )
        self._session.add(job)
        if connection.quota_remaining is not None:
            connection.quota_remaining -= 1
            if connection.quota_reset_at is None:
                connection.quota_reset_at = now + timedelta(days=1)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="media.job.queued",
            aggregate_type="media_operation_job",
            aggregate_id=job.id,
            details={"operation": job.operation, "estimated_cost": str(job.estimated_cost)},
        )
        return job, True

    async def get_operation_job(
        self, principal: Principal, job_id: UUID
    ) -> MediaOperationJob:
        await self._scope(principal.workspace_id)
        value = await self._session.scalar(
            select(MediaOperationJob).where(
                MediaOperationJob.workspace_id == principal.workspace_id,
                MediaOperationJob.id == job_id,
            )
        )
        if value is None:
            raise _not_found("MEDIA_JOB", "이미지 작업")
        return value

    async def command_operation_job(
        self,
        principal: Principal,
        job_id: UUID,
        data: MediaJobCommandRequest,
        *,
        command_kind: str,
    ) -> MediaOperationJob:
        """Persist an idempotent cancel/retry command before changing job state."""

        await self._scope(principal.workspace_id)
        if command_kind not in {"CANCEL", "RETRY"}:
            raise AppError("MEDIA_COMMAND_INVALID", "이미지 작업 명령이 올바르지 않습니다.", 422)
        request_hash = canonical_hash(
            {"command_kind": command_kind, "reason": data.reason}
        )
        existing = await self._session.scalar(
            select(MediaJobCommand).where(
                MediaJobCommand.workspace_id == principal.workspace_id,
                MediaJobCommand.job_id == job_id,
                MediaJobCommand.actor_id == principal.subject_id,
                MediaJobCommand.command_kind == command_kind,
                MediaJobCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 멱등키가 다른 이미지 작업 명령에 사용되었습니다.",
                    409,
                )
            return await self.get_operation_job(principal, job_id)
        job = await self._session.scalar(
            select(MediaOperationJob)
            .where(
                MediaOperationJob.workspace_id == principal.workspace_id,
                MediaOperationJob.id == job_id,
            )
            .with_for_update()
        )
        if job is None:
            raise _not_found("MEDIA_JOB", "이미지 작업")
        if (
            job.requested_by != principal.subject_id
            and "media:manage" not in principal.permissions
        ):
            raise AppError(
                "MEDIA_JOB_CONTROL_DENIED",
                "다른 사용자의 이미지 작업을 제어할 권한이 없습니다.",
                403,
            )
        current = JobState(job.state)
        if command_kind == "CANCEL":
            if current in TERMINAL_JOB_STATES:
                raise AppError(
                    "MEDIA_JOB_TERMINAL",
                    "종료된 이미지 작업은 취소할 수 없습니다.",
                    409,
                )
            target = JobState.CANCEL_REQUESTED
            if current is not target:
                ensure_job_transition(current, target)
                job.state = target.value
        else:
            if current is not JobState.RETRYABLE_FAILED:
                raise AppError(
                    "MEDIA_JOB_NOT_RETRYABLE",
                    "재시도 가능한 실패 상태의 이미지 작업만 재시도할 수 있습니다.",
                    409,
                )
            if job.attempt >= job.max_attempts:
                raise AppError(
                    "MEDIA_RETRY_LIMIT_REACHED",
                    "이미지 작업의 최대 재시도 횟수에 도달했습니다.",
                    409,
                )
            target = JobState.QUEUED
            ensure_job_transition(current, target)
            job.state = target.value
            job.error_code = None
            job.error_detail = None
        command = MediaJobCommand(
            workspace_id=principal.workspace_id,
            job_id=job.id,
            actor_id=principal.subject_id,
            command_kind=command_kind,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            from_state=current.value,
            to_state=target.value,
            reason=data.reason,
        )
        self._session.add(command)
        await self._session.flush()
        await self._record(
            principal=principal,
            action=f"media.job.{command_kind.casefold()}",
            aggregate_type="media_operation_job",
            aggregate_id=job.id,
            details={"from_state": current.value, "to_state": target.value},
        )
        return job

    async def create_plan(
        self, principal: Principal, data: ImagePlanCreate
    ) -> tuple[MediaPlanVersion, list[MediaPlanItem]]:
        await self._scope(principal.workspace_id)
        content_version = await self._session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == principal.workspace_id,
                ContentVersion.content_id == data.content_id,
                ContentVersion.id == data.content_version_id,
            )
        )
        if content_version is None:
            raise _not_found("CONTENT_VERSION", "콘텐츠 버전")
        if content_version.content_hash != data.content_hash:
            raise AppError("CONTENT_VERSION_HASH_MISMATCH", "콘텐츠 버전 Hash가 다릅니다.", 409)
        content = await self._session.scalar(
            select(ContentItem).where(
                ContentItem.workspace_id == principal.workspace_id,
                ContentItem.id == data.content_id,
                ContentItem.deleted_at.is_(None),
            )
        )
        if content is None:
            raise _not_found("CONTENT", "콘텐츠")
        workspace = await self._session.scalar(
            select(Workspace).where(Workspace.id == principal.workspace_id)
        )
        if workspace is None:
            raise _not_found("WORKSPACE", "워크스페이스")
        media_policy = workspace.generation_policy.get("media")
        if not isinstance(media_policy, dict):
            raise AppError(
                "MEDIA_PLAN_POLICY_MISSING",
                "서버에서 관리하는 이미지 계획 정책을 확인할 수 없습니다.",
                503,
            )
        count_policy = media_policy.get("count_policy")
        if not isinstance(count_policy, dict):
            raise AppError(
                "MEDIA_COUNT_POLICY_MISSING",
                "서버에서 관리하는 이미지 수 정책을 확인할 수 없습니다.",
                503,
            )
        try:
            minimum_count = max(0, int(count_policy["minimum"]))
            maximum_count = max(minimum_count, int(count_policy["maximum"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                "MEDIA_COUNT_POLICY_INVALID",
                "이미지 수 정책의 최소·최대 값이 올바르지 않습니다.",
                503,
            ) from exc
        if not minimum_count <= data.recommended_count <= maximum_count:
            raise AppError(
                "MEDIA_PLAN_COUNT_BLOCKED",
                "이미지 수가 서버의 채널 정책 범위를 벗어났습니다.",
                422,
                remediation={
                    "minimum": minimum_count,
                    "maximum": maximum_count,
                },
            )
        candidate_ids = {
            candidate_id
            for item in data.items
            for candidate_id in item.candidate_asset_ids
        }
        if candidate_ids:
            available_candidates = set(
                await self._session.scalars(
                    select(MediaAsset.id).where(
                        MediaAsset.workspace_id == principal.workspace_id,
                        MediaAsset.id.in_(candidate_ids),
                        MediaAsset.state == MediaAssetState.READY.value,
                        MediaAsset.deleted_at.is_(None),
                    )
                )
            )
            if available_candidates != candidate_ids:
                raise AppError(
                    "MEDIA_PLAN_CANDIDATE_INVALID",
                    "계획 후보에는 현재 워크스페이스의 준비된 이미지만 포함할 수 있습니다.",
                    422,
                )
        brand_snapshot = await SQLAlchemyPlanningReferenceResolver(
            self._session
        ).brand_snapshot(principal.workspace_id, content.brand_id)
        server_prohibited = media_policy.get("prohibited_elements", [])
        if not isinstance(server_prohibited, list) or not all(
            isinstance(value, str) for value in server_prohibited
        ):
            raise AppError(
                "MEDIA_PLAN_POLICY_INVALID",
                "이미지 금지 요소 정책이 올바르지 않습니다.",
                503,
            )
        prohibited_elements = list(
            dict.fromkeys([*server_prohibited, *data.prohibited_elements])
        )
        payload = {
            **data.model_dump(mode="json"),
            "channel": content.channel,
            "count_policy_snapshot": count_policy,
            "brand_snapshot": brand_snapshot or {},
            "generation_policy_snapshot": media_policy,
            "prohibited_elements": prohibited_elements,
        }
        plan = MediaPlanVersion(
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            channel=content.channel,
            recommended_count=data.recommended_count,
            count_policy_snapshot=count_policy,
            brand_snapshot=brand_snapshot or {},
            generation_policy_snapshot=media_policy,
            prohibited_elements=prohibited_elements,
            plan_hash=canonical_hash(payload),
            created_by=principal.subject_id,
        )
        self._session.add(plan)
        await self._session.flush()
        items = [
            MediaPlanItem(
                workspace_id=principal.workspace_id,
                plan_id=plan.id,
                sequence=index,
                section_key=item.section_key,
                need_kind=item.need_kind.value,
                reason=item.reason,
                requires_real_photo=item.requires_real_photo,
                generation_allowed=item.generation_allowed,
                generation_prompt=item.generation_prompt,
                prohibited_elements=item.prohibited_elements,
                alt_text_plan=item.alt_text_plan,
                caption_plan=item.caption_plan,
                aspect_ratio=item.aspect_ratio,
                placement_json=item.placement,
                candidate_asset_ids=[str(value) for value in item.candidate_asset_ids],
                duplicate_warning=item.duplicate_warning,
                performance_ref=item.performance_ref,
            )
            for index, item in enumerate(data.items, start=1)
        ]
        self._session.add_all(items)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="media.plan.created",
            aggregate_type="media_plan",
            aggregate_id=plan.id,
            details={"content_version_id": str(data.content_version_id), "count": len(items)},
        )
        return plan, items

    async def get_plan(
        self, principal: Principal, plan_id: UUID
    ) -> tuple[MediaPlanVersion, list[MediaPlanItem]]:
        await self._scope(principal.workspace_id)
        plan = await self._session.scalar(
            select(MediaPlanVersion).where(
                MediaPlanVersion.workspace_id == principal.workspace_id,
                MediaPlanVersion.id == plan_id,
            )
        )
        if plan is None:
            raise _not_found("MEDIA_PLAN", "이미지 계획")
        items = list(
            await self._session.scalars(
                select(MediaPlanItem)
                .where(
                    MediaPlanItem.workspace_id == principal.workspace_id,
                    MediaPlanItem.plan_id == plan_id,
                )
                .order_by(MediaPlanItem.sequence)
            )
        )
        return plan, items

    async def select_plan_asset(
        self,
        principal: Principal,
        item_id: UUID,
        data: ImageSelection,
    ) -> MediaPlanItem:
        await self._scope(principal.workspace_id)
        item = await self._session.scalar(
            select(MediaPlanItem)
            .where(
                MediaPlanItem.workspace_id == principal.workspace_id,
                MediaPlanItem.id == item_id,
            )
            .with_for_update()
        )
        if item is None:
            raise _not_found("MEDIA_PLAN_ITEM", "이미지 계획 항목")
        if item.lock_version != data.expected_lock_version:
            raise AppError("OPTIMISTIC_LOCK_CONFLICT", "이미지 계획 항목이 변경되었습니다.", 409)
        asset = await self._asset(principal.workspace_id, data.asset_id)
        version = await self._version(principal.workspace_id, data.asset_id, data.version_id)
        if asset.state != MediaAssetState.READY.value:
            raise AppError("MEDIA_ASSET_NOT_READY", "준비되지 않은 이미지는 선택할 수 없습니다.", 409)
        ensure_real_photo_policy(
            requires_real_photo=item.requires_real_photo,
            asset_ai_generated=version.ai_generated,
        )
        plan = await self._session.scalar(
            select(MediaPlanVersion).where(
                MediaPlanVersion.workspace_id == principal.workspace_id,
                MediaPlanVersion.id == item.plan_id,
            )
        )
        if plan is None:
            raise _not_found("MEDIA_PLAN", "이미지 계획")
        ledger, revision = await self.current_license(principal, data.asset_id)
        if ledger.state != LicenseState.ACTIVE.value:
            raise AppError(
                "MEDIA_LICENSE_INACTIVE",
                "활성 라이선스가 없는 이미지는 선택할 수 없습니다.",
                409,
            )
        rights = RightsSnapshot(
            state=revision.state,
            license_type=revision.license_type,
            commercial_allowed=revision.commercial_allowed,
            editorial_allowed=revision.editorial_allowed,
            allowed_channels=tuple(revision.allowed_channels),
            allowed_regions=tuple(revision.allowed_regions),
            valid_from=revision.valid_from,
            valid_until=revision.valid_until,
            attribution_required=revision.attribution_required,
            attribution_text=revision.attribution_text,
            terms_hash=revision.snapshot_hash,
        )
        decision = evaluate_usage_rights(
            rights,
            channel=plan.channel,
            region=data.region,
            usage_mode=data.usage_mode,
        )
        if not decision.allowed:
            raise AppError(
                "MEDIA_SELECTION_RIGHTS_BLOCKED",
                "이미지 계획의 채널·지역·사용 방식에 라이선스가 맞지 않습니다.",
                409,
                fields=[
                    {"path": "license", "reason": reason}
                    for reason in decision.reasons
                ],
            )
        item.selected_asset_id = data.asset_id
        item.selected_version_id = data.version_id
        item.selection_state = ImageSelectionState.SELECTED.value
        item.selected_by = principal.subject_id
        item.selected_at = datetime.now(UTC)
        await self._session.flush()
        return item

    async def register_usage(
        self, principal: Principal, data: MediaUsageCreate
    ) -> MediaUsage:
        await self._scope(principal.workspace_id)
        content_version = await self._session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == principal.workspace_id,
                ContentVersion.content_id == data.content_id,
                ContentVersion.id == data.content_version_id,
            )
        )
        if content_version is None:
            raise _not_found("CONTENT_VERSION", "콘텐츠 버전")
        if content_version.content_hash != data.content_hash:
            raise AppError("CONTENT_VERSION_HASH_MISMATCH", "콘텐츠 버전 Hash가 다릅니다.", 409)
        asset = await self._asset(principal.workspace_id, data.asset_id)
        version = await self._version(
            principal.workspace_id, data.asset_id, data.media_version_id
        )
        revision = await self._session.scalar(
            select(MediaLicenseRevision).where(
                MediaLicenseRevision.workspace_id == principal.workspace_id,
                MediaLicenseRevision.asset_id == data.asset_id,
                MediaLicenseRevision.id == data.license_revision_id,
            )
        )
        if revision is None:
            raise _not_found("MEDIA_LICENSE_REVISION", "미디어 라이선스 버전")
        ledger, current_revision = await self.current_license(
            principal,
            data.asset_id,
        )
        if (
            ledger.state != LicenseState.ACTIVE.value
            or ledger.current_revision_id != revision.id
            or current_revision.id != revision.id
        ):
            raise AppError(
                "MEDIA_LICENSE_REVISION_STALE",
                "현재 활성 라이선스 버전만 새 이미지 사용에 고정할 수 있습니다.",
                409,
            )
        rights = RightsSnapshot(
            state=revision.state,
            license_type=revision.license_type,
            commercial_allowed=revision.commercial_allowed,
            editorial_allowed=revision.editorial_allowed,
            allowed_channels=tuple(revision.allowed_channels),
            allowed_regions=tuple(revision.allowed_regions),
            valid_from=revision.valid_from,
            valid_until=revision.valid_until,
            attribution_required=revision.attribution_required,
            attribution_text=revision.attribution_text,
            terms_hash=revision.snapshot_hash,
        )
        decision = evaluate_usage_rights(
            rights,
            channel=data.channel,
            region=data.region,
            usage_mode=UsageMode(data.usage_mode),
        )
        if not decision.allowed:
            raise AppError(
                "MEDIA_USAGE_RIGHTS_BLOCKED",
                "이미지 사용 범위가 고정된 라이선스 버전과 일치하지 않습니다.",
                409,
                fields=[{"path": "license", "reason": reason} for reason in decision.reasons],
            )
        if asset.ai_disclosure_required or version.ai_generated:
            disclosure = (data.caption or "").casefold()
            if not any(
                marker in disclosure
                for marker in ("ai 생성", "인공지능 생성", "generated by ai", "ai-generated")
            ):
                raise AppError(
                    "MEDIA_AI_DISCLOSURE_REQUIRED",
                    "AI 생성 이미지에는 명시적인 AI 생성 고지가 필요합니다.",
                    422,
                )
        rights_snapshot = {
            "license_revision_id": str(revision.id),
            "license_type": revision.license_type,
            "state": revision.state,
            "commercial_allowed": revision.commercial_allowed,
            "editorial_allowed": revision.editorial_allowed,
            "allowed_channels": revision.allowed_channels,
            "allowed_regions": revision.allowed_regions,
            "valid_from": revision.valid_from.isoformat() if revision.valid_from else None,
            "valid_until": revision.valid_until.isoformat() if revision.valid_until else None,
            "attribution_required": revision.attribution_required,
            "attribution_text": revision.attribution_text,
            "license_snapshot_hash": revision.snapshot_hash,
        }
        usage = MediaUsage(
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            content_version_id=data.content_version_id,
            content_hash=data.content_hash,
            asset_id=data.asset_id,
            media_version_id=data.media_version_id,
            license_revision_id=data.license_revision_id,
            placement_key=data.placement_key,
            channel=data.channel,
            region=data.region,
            usage_mode=data.usage_mode.value,
            alt_text=data.alt_text,
            caption=data.caption,
            attribution_text=decision.required_attribution,
            rights_snapshot=rights_snapshot,
            rights_snapshot_hash=canonical_hash(rights_snapshot),
            created_by=principal.subject_id,
        )
        self._session.add(usage)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="media.usage.registered",
            aggregate_type="media_usage",
            aggregate_id=usage.id,
            details={"content_version_id": str(data.content_version_id), "asset_id": str(data.asset_id)},
        )
        return usage

    async def usage_report(
        self,
        principal: Principal,
        *,
        content_version_id: UUID | None,
        asset_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[MediaUsage]:
        await self._scope(principal.workspace_id)
        query = select(MediaUsage).where(
            MediaUsage.workspace_id == principal.workspace_id
        )
        if content_version_id is not None:
            query = query.where(MediaUsage.content_version_id == content_version_id)
        if asset_id is not None:
            query = query.where(MediaUsage.asset_id == asset_id)
        return list(
            await self._session.scalars(
                query.order_by(MediaUsage.created_at.desc()).limit(limit).offset(offset)
            )
        )

    async def delete_asset(
        self, principal: Principal, asset_id: UUID, data: MediaDeleteRequest
    ) -> MediaAsset:
        await self._scope(principal.workspace_id)
        asset = await self._asset(principal.workspace_id, asset_id, for_update=True)
        if asset.lock_version != data.expected_lock_version:
            raise AppError("OPTIMISTIC_LOCK_CONFLICT", "미디어 자산이 변경되었습니다.", 409)
        usage_count = int(
            await self._session.scalar(
                select(func.count(MediaUsage.id)).where(
                    MediaUsage.workspace_id == principal.workspace_id,
                    MediaUsage.asset_id == asset_id,
                )
            )
            or 0
        )
        plan_reference_count = int(
            await self._session.scalar(
                select(func.count(MediaPlanItem.id)).where(
                    MediaPlanItem.workspace_id == principal.workspace_id,
                    MediaPlanItem.selected_asset_id == asset_id,
                )
            )
            or 0
        )
        job_reference_count = int(
            await self._session.scalar(
                select(func.count(MediaOperationJob.id)).where(
                    MediaOperationJob.workspace_id == principal.workspace_id,
                    or_(
                        MediaOperationJob.source_asset_id == asset_id,
                        MediaOperationJob.result_asset_id == asset_id,
                    ),
                )
            )
            or 0
        )
        if data.acknowledge_usage_count != usage_count:
            raise AppError(
                "MEDIA_USAGE_ACK_MISMATCH",
                "현재 이미지 사용 참조 수를 확인한 뒤 다시 요청해 주세요.",
                409,
                remediation={"usage_count": usage_count},
            )
        if usage_count:
            raise AppError(
                "MEDIA_ASSET_IN_USE",
                "콘텐츠에서 사용 중인 이미지는 삭제할 수 없습니다.",
                409,
                remediation={"usage_count": usage_count},
            )
        if plan_reference_count or job_reference_count:
            raise AppError(
                "MEDIA_ASSET_REFERENCED",
                "이미지 계획 또는 작업 이력에서 참조 중인 이미지는 삭제할 수 없습니다.",
                409,
                remediation={
                    "plan_reference_count": plan_reference_count,
                    "job_reference_count": job_reference_count,
                },
            )
        asset.state = MediaAssetState.DELETED.value
        asset.deleted_at = datetime.now(UTC)
        asset.review_reason = data.reason
        await self._record(
            principal=principal,
            action="media.asset.deleted",
            aggregate_type="media_asset",
            aggregate_id=asset.id,
            details={"reason": data.reason},
        )
        return asset


def _not_found(code: str, label: str) -> AppError:
    return AppError(
        code=f"{code}_NOT_FOUND",
        message=f"{label}을(를) 찾을 수 없습니다.",
        status_code=404,
    )
