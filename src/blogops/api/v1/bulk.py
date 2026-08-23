"""Canonical bulk input, preview, queue and row-action API."""

import base64
import binascii
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.bulk.ingestion import (
    XlsxSnapshotParser,
    verify_uploaded_bulk_snapshot,
)
from blogops.domain.bulk.parsing import preview_csv
from blogops.domain.bulk.providers import BulkBudgetGate
from blogops.domain.bulk.schemas import (
    BulkCommandRequest,
    BulkExportRequest,
    BulkInputRead,
    BulkJobCreate,
    BulkJobRead,
    BulkMappingCreate,
    BulkMappingRead,
    BulkRowRead,
    BulkRowsCommand,
    BulkScheduleCreate,
    BulkScheduleRead,
    BulkUploadComplete,
    BulkUploadGrant,
    BulkUploadInitiate,
    CsvPreviewRead,
    CsvPreviewRequest,
    PreviewRowRead,
)
from blogops.domain.bulk.service import BulkService
from blogops.domain.bulk.tasks import enqueue_bulk_job
from blogops.domain.knowledge.adapters import ClamAVScanner, MalwareScanner
from blogops.domain.media.storage import (
    PrivateObjectStorage,
    get_private_object_storage,
)

router = APIRouter(prefix="/bulk", tags=["bulk"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
BulkReader = Annotated[Principal, Depends(require_permissions(Permission.BULK_READ))]
BulkWriter = Annotated[Principal, Depends(require_permissions(Permission.BULK_WRITE))]
BulkApprover = Annotated[Principal, Depends(require_permissions(Permission.BULK_APPROVE))]
BulkExporter = Annotated[
    Principal,
    Depends(require_permissions(Permission.BULK_READ, Permission.BULK_EXPORT)),
]


def bulk_service(session: TenantSession) -> BulkService:
    return BulkService(session)


def bulk_budget_gate(session: TenantSession) -> BulkBudgetGate:
    """Build the billing-backed gate in the request's tenant transaction."""

    from blogops.domain.billing.adapters import create_bulk_budget_gate

    return create_bulk_budget_gate(session)


def bulk_malware_scanner() -> MalwareScanner:
    settings = get_settings()
    return ClamAVScanner(
        settings.clamav_host,
        settings.clamav_port,
        settings.clamav_timeout_seconds,
    )


def bulk_xlsx_parser() -> XlsxSnapshotParser | None:
    """Deployment wiring may override this with an approved sandboxed parser."""

    return None


Service = Annotated[BulkService, Depends(bulk_service)]
BudgetGate = Annotated[BulkBudgetGate, Depends(bulk_budget_gate)]
Storage = Annotated[PrivateObjectStorage, Depends(get_private_object_storage)]
Scanner = Annotated[MalwareScanner, Depends(bulk_malware_scanner)]
XlsxParser = Annotated[XlsxSnapshotParser | None, Depends(bulk_xlsx_parser)]


@router.post("/preview/csv", response_model=CsvPreviewRead)
async def preview_bulk_csv(
    data: CsvPreviewRequest,
    _principal: BulkWriter,
) -> CsvPreviewRead:
    if data.content is not None:
        content = data.content.encode("utf-8")
    else:
        try:
            content = base64.b64decode(data.content_base64 or "", validate=True)
        except binascii.Error as exc:
            raise AppError("BULK_BASE64_INVALID", "CSV Base64 값이 올바르지 않습니다.", 422) from exc
    preview = preview_csv(
        content,
        column_mapping=data.column_mapping,
        required_variables=tuple(data.required_variables),
        preview_limit=data.preview_limit,
        delimiter=data.delimiter,
    )
    return CsvPreviewRead(
        encoding=preview.encoding,
        delimiter=preview.delimiter,
        headers=list(preview.headers),
        total_rows=preview.total_rows,
        preview_rows=[
            PreviewRowRead(
                row_no=value.row_no,
                values=dict(value.values),
                mapped_values=dict(value.mapped_values),
                input_hash=value.input_hash,
                duplicate_of_row_no=value.duplicate_of_row_no,
                errors=list(value.errors),
            )
            for value in preview.preview_rows
        ],
        invalid_rows=preview.invalid_rows,
        exact_duplicates=preview.exact_duplicates,
    )


@router.post(
    "/uploads",
    response_model=BulkUploadGrant,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_bulk_upload(
    data: BulkUploadInitiate,
    principal: BulkWriter,
    storage: Storage,
) -> BulkUploadGrant:
    settings = get_settings()
    if data.size_bytes > settings.knowledge_max_upload_bytes:
        raise AppError(
            "BULK_UPLOAD_TOO_LARGE",
            "대량 입력 파일 용량이 정책 한도를 초과했습니다.",
            422,
            fields=[
                {
                    "path": "size_bytes",
                    "reason": f"limit={settings.knowledge_max_upload_bytes}",
                }
            ],
        )
    upload_id = uuid4()
    grant = await storage.initiate_upload(
        workspace_id=principal.workspace_id,
        namespace="bulk",
        owner_id=upload_id,
        filename=data.filename,
        content_type=data.mime_type,
    )
    return BulkUploadGrant(
        upload_id=upload_id,
        object_ref=grant.object_ref,
        upload_url=grant.upload_url,
        expires_in=grant.expires_in,
    )


async def _complete_bulk_upload(
    *,
    data: BulkUploadComplete,
    principal: Principal,
    service: BulkService,
    storage: PrivateObjectStorage,
    scanner: MalwareScanner,
    xlsx_parser: XlsxSnapshotParser | None,
) -> BulkInputRead:
    settings = get_settings()
    snapshot = await verify_uploaded_bulk_snapshot(
        workspace_id=principal.workspace_id,
        upload=data,
        storage=storage,
        scanner=scanner,
        scanner_name="clamav",
        scanner_version="clamd-instream-v1",
        max_upload_bytes=settings.knowledge_max_upload_bytes,
        xlsx_parser=xlsx_parser,
    )
    try:
        input_file = await service.register_input(principal, snapshot)
    except Exception:
        await storage.delete(snapshot.object_ref)
        raise
    if input_file.object_ref != snapshot.object_ref:
        await storage.delete(snapshot.object_ref)
    return BulkInputRead.model_validate(input_file)


@router.post(
    "/uploads/{upload_id}/complete",
    response_model=BulkInputRead,
    status_code=status.HTTP_201_CREATED,
)
async def complete_bulk_upload(
    upload_id: UUID,
    data: BulkUploadComplete,
    principal: BulkWriter,
    service: Service,
    storage: Storage,
    scanner: Scanner,
    xlsx_parser: XlsxParser,
) -> BulkInputRead:
    if upload_id != data.upload_id:
        raise AppError(
            "BULK_UPLOAD_ID_MISMATCH",
            "업로드 완료 요청의 ID가 발급된 경로와 일치하지 않습니다.",
            409,
        )
    return await _complete_bulk_upload(
        data=data,
        principal=principal,
        service=service,
        storage=storage,
        scanner=scanner,
        xlsx_parser=xlsx_parser,
    )


@router.post(
    "/input-files",
    response_model=BulkInputRead,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def complete_bulk_input_legacy(
    data: BulkUploadComplete,
    principal: BulkWriter,
    service: Service,
    storage: Storage,
    scanner: Scanner,
    xlsx_parser: XlsxParser,
) -> BulkInputRead:
    """Trusted compatibility alias; client-supplied CLEAN metadata is not accepted."""

    return await _complete_bulk_upload(
        data=data,
        principal=principal,
        service=service,
        storage=storage,
        scanner=scanner,
        xlsx_parser=xlsx_parser,
    )


@router.post(
    "/mappings",
    response_model=BulkMappingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bulk_mapping(
    data: BulkMappingCreate,
    principal: BulkWriter,
    service: Service,
) -> BulkMappingRead:
    return BulkMappingRead.model_validate(await service.create_mapping(principal, data))


@router.post("/jobs", response_model=BulkJobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_bulk_job(
    data: BulkJobCreate,
    principal: BulkWriter,
    service: Service,
    budget_gate: BudgetGate,
    background_tasks: BackgroundTasks,
) -> BulkJobRead:
    job, enqueue_needed = await service.create_job(
        principal,
        data,
        budget_gate=budget_gate,
    )
    if enqueue_needed:
        background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job.id)
    return BulkJobRead.model_validate(job)


@router.get("/jobs", response_model=list[BulkJobRead])
async def list_bulk_jobs(
    principal: BulkReader,
    service: Service,
    job_state: str | None = Query(default=None, alias="state"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BulkJobRead]:
    return [
        BulkJobRead.model_validate(value)
        for value in await service.list_jobs(
            principal,
            state=job_state,
            limit=limit,
            offset=offset,
        )
    ]


@router.get("/jobs/{job_id}", response_model=BulkJobRead)
async def get_bulk_job(
    job_id: UUID,
    principal: BulkReader,
    service: Service,
) -> BulkJobRead:
    return BulkJobRead.model_validate(await service.get_job(principal, job_id))


@router.get("/jobs/{job_id}/rows", response_model=list[BulkRowRead])
async def list_bulk_rows(
    job_id: UUID,
    principal: BulkReader,
    service: Service,
    row_state: str | None = Query(default=None, alias="state"),
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BulkRowRead]:
    return [
        BulkRowRead.model_validate(value)
        for value in await service.list_rows(
            principal,
            job_id,
            state=row_state,
            limit=limit,
            offset=offset,
        )
    ]


@router.post("/jobs/{job_id}/pause", response_model=BulkJobRead)
async def pause_bulk_job(
    job_id: UUID,
    data: BulkCommandRequest,
    principal: BulkWriter,
    service: Service,
) -> BulkJobRead:
    return BulkJobRead.model_validate(await service.pause_job(principal, job_id, data))


@router.post("/jobs/{job_id}/resume", response_model=BulkJobRead)
async def resume_bulk_job(
    job_id: UUID,
    data: BulkCommandRequest,
    principal: BulkWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> BulkJobRead:
    job = await service.resume_job(principal, job_id, data)
    background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job.id)
    return BulkJobRead.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=BulkJobRead)
async def cancel_bulk_job(
    job_id: UUID,
    data: BulkCommandRequest,
    principal: BulkWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> BulkJobRead:
    job = await service.cancel_job(principal, job_id, data)
    background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job.id)
    return BulkJobRead.model_validate(job)


@router.post("/jobs/{job_id}/rows/retry", response_model=list[BulkRowRead])
async def retry_bulk_rows(
    job_id: UUID,
    data: BulkRowsCommand,
    principal: BulkWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> list[BulkRowRead]:
    rows = [
        BulkRowRead.model_validate(value)
        for value in await service.retry_rows(
            principal,
            job_id,
            data,
            regenerate=False,
        )
    ]
    background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job_id)
    return rows


@router.post("/jobs/{job_id}/rows/regenerate", response_model=list[BulkRowRead])
async def regenerate_bulk_rows(
    job_id: UUID,
    data: BulkRowsCommand,
    principal: BulkWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> list[BulkRowRead]:
    rows = [
        BulkRowRead.model_validate(value)
        for value in await service.retry_rows(
            principal,
            job_id,
            data,
            regenerate=True,
        )
    ]
    background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job_id)
    return rows


@router.post("/jobs/{job_id}/rows/approve", response_model=list[BulkRowRead])
async def approve_bulk_rows(
    job_id: UUID,
    data: BulkRowsCommand,
    principal: BulkApprover,
    service: Service,
    background_tasks: BackgroundTasks,
) -> list[BulkRowRead]:
    rows = [
        BulkRowRead.model_validate(value)
        for value in await service.approve_rows(principal, job_id, data)
    ]
    background_tasks.add_task(enqueue_bulk_job, principal.workspace_id, job_id)
    return rows


@router.post("/jobs/{job_id}/exports", status_code=status.HTTP_202_ACCEPTED)
async def request_bulk_export(
    job_id: UUID,
    data: BulkExportRequest,
    principal: BulkExporter,
    service: Service,
) -> dict[str, Any]:
    request_id = await service.request_export(
        principal,
        job_id,
        export_kind=data.export_kind.value,
        include_states=data.include_states,
        idempotency_key=data.idempotency_key,
    )
    return {"request_id": request_id, "state": "QUEUED"}


@router.post(
    "/schedules",
    response_model=BulkScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bulk_schedule(
    data: BulkScheduleCreate,
    principal: BulkWriter,
    service: Service,
) -> BulkScheduleRead:
    return BulkScheduleRead.model_validate(await service.create_schedule(principal, data))
