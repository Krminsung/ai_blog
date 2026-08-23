"""Knowledge application services; callers provide an RLS-scoped transaction."""

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.domain.knowledge.enums import (
    KnowledgeJobState,
    KnowledgeJobType,
    RightsStatus,
    SourceState,
    SourceType,
)
from blogops.domain.knowledge.models import (
    KnowledgeJob,
    KnowledgeSource,
    SourceChunk,
    SourceDocument,
    SourceVersion,
)
from blogops.domain.knowledge.adapters import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    MalwareScanner,
    MalwareStatus,
    OcrProvider,
    SafeFetcher,
)
from blogops.domain.knowledge.parsing import (
    ExtractedBlock,
    ParsedDocument,
    mask_pii,
    parse_document,
    parse_fetched_document,
    semantic_chunks,
)
from blogops.domain.knowledge.schemas import SearchResult, SourceCreate, UploadInitiateRequest
from blogops.domain.knowledge.security import validate_source_url
from blogops.domain.knowledge.storage import ObjectStorage, UploadGrant
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _rights_confirmed(status: RightsStatus) -> bool:
    return status not in {RightsStatus.UNCONFIRMED, RightsStatus.PROHIBITED}


async def create_source(
    session: AsyncSession, *, principal: Principal, data: SourceCreate
) -> tuple[KnowledgeSource, KnowledgeJob | None]:
    uri = data.uri
    canonical_uri_hash: str | None = None
    if uri:
        validated = validate_source_url(uri)
        uri = validated.normalized
        canonical_uri_hash = _content_hash(uri)
    content = data.content
    metadata = dict(data.metadata)
    if data.faq_items:
        content = "\n\n".join(
            f"질문: {item.question}\n답변: {item.answer}" for item in data.faq_items
        )
        metadata["faq_items"] = [item.model_dump(mode="json") for item in data.faq_items]
    if canonical_uri_hash:
        duplicate = await session.scalar(
            select(KnowledgeSource.id).where(
                KnowledgeSource.workspace_id == principal.workspace_id,
                KnowledgeSource.canonical_uri_hash == canonical_uri_hash,
                KnowledgeSource.deleted_at.is_(None),
            )
        )
        if duplicate is not None:
            raise AppError(
                "KNOWLEDGE_SOURCE_DUPLICATE",
                "동일한 URL의 지식 소스가 이미 등록되어 있습니다.",
                409,
                remediation={"existing_source_id": str(duplicate)},
            )
    source = KnowledgeSource(
        workspace_id=principal.workspace_id,
        created_by=principal.subject_id,
        source_type=data.source_type.value,
        name=data.name,
        uri=uri,
        canonical_uri_hash=canonical_uri_hash,
        rights_status=data.rights_status.value,
        use_scope=data.use_scope.value,
        quality_grade=data.quality_grade.value,
        rights_confirmed_at=datetime.now(UTC) if _rights_confirmed(data.rights_status) else None,
        state=SourceState.CREATED.value,
        metadata_json=metadata,
    )
    session.add(source)
    await session.flush()

    job: KnowledgeJob | None = None
    if content:
        await _create_inline_version(
            session,
            source=source,
            content=content,
            embeddings=HashingEmbeddingProvider(),
        )
    else:
        source.state = SourceState.QUEUED.value
        job = await _queue_job(
            session, source=source, principal=principal, job_type=KnowledgeJobType.FETCH
        )

    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="knowledge.source.created",
        target_type="knowledge_source",
        target_id=str(source.id),
        details={"type": source.source_type, "rights_status": source.rights_status},
    )
    return source, job


async def _create_inline_version(
    session: AsyncSession,
    *,
    source: KnowledgeSource,
    content: str,
    embeddings: EmbeddingProvider | None = None,
) -> SourceVersion:
    content = content.strip()
    digest = _content_hash(content)
    existing = await session.scalar(
        select(SourceVersion).where(
            SourceVersion.workspace_id == source.workspace_id,
            SourceVersion.source_id == source.id,
            SourceVersion.content_hash == digest,
        )
    )
    if existing:
        source.current_version_id = existing.id
        source.state = SourceState.READY.value
        return existing
    latest = await session.scalar(
        select(func.coalesce(func.max(SourceVersion.version), 0)).where(
            SourceVersion.workspace_id == source.workspace_id,
            SourceVersion.source_id == source.id,
        )
    )
    version = SourceVersion(
        workspace_id=source.workspace_id,
        source_id=source.id,
        version=int(latest or 0) + 1,
        content_hash=digest,
        metadata_json={"origin": "inline"},
    )
    session.add(version)
    await session.flush()
    stored_content, pii_detected = mask_pii(content)
    document = SourceDocument(
        workspace_id=source.workspace_id,
        source_version_id=version.id,
        title=source.name,
        text=stored_content,
        structure_json={"kind": source.source_type.lower()},
        parser_name="inline-text",
        parser_version="1",
        pii_detected=pii_detected,
    )
    session.add(document)
    await session.flush()
    blocks = (ExtractedBlock(content, {"type": "inline", "index": 1}),)
    chunks = semantic_chunks(blocks)
    vectors = await embeddings.embed([chunk.text for chunk in chunks]) if embeddings else None
    for index, chunk in enumerate(chunks):
        session.add(
            SourceChunk(
                workspace_id=source.workspace_id,
                source_version_id=version.id,
                document_id=document.id,
                sequence=chunk.sequence,
                locator_json=chunk.locator,
                text=chunk.text,
                text_hash=chunk.text_hash,
                token_estimate=chunk.token_estimate,
                quality_grade=source.quality_grade,
                pii_masked=chunk.pii_masked,
                embedding=vectors[index] if vectors else None,
                embedding_model=embeddings.model if embeddings else None,
                embedding_version=embeddings.version if embeddings else None,
            )
        )
    source.current_version_id = version.id
    source.state = SourceState.READY.value
    source.last_synced_at = datetime.now(UTC)
    await session.flush()
    return version


async def _store_parsed_version(
    session: AsyncSession,
    *,
    source: KnowledgeSource,
    parsed: ParsedDocument,
    raw_object_key: str | None,
    content_hash: str,
    embeddings: EmbeddingProvider,
    used_ocr: bool,
) -> SourceVersion:
    existing = await session.scalar(
        select(SourceVersion).where(
            SourceVersion.workspace_id == source.workspace_id,
            SourceVersion.source_id == source.id,
            SourceVersion.content_hash == content_hash,
        )
    )
    if existing:
        source.current_version_id = existing.id
        source.state = SourceState.READY.value
        source.last_synced_at = datetime.now(UTC)
        return existing

    latest = await session.scalar(
        select(func.coalesce(func.max(SourceVersion.version), 0)).where(
            SourceVersion.workspace_id == source.workspace_id,
            SourceVersion.source_id == source.id,
        )
    )
    version = SourceVersion(
        workspace_id=source.workspace_id,
        source_id=source.id,
        version=int(latest or 0) + 1,
        content_hash=content_hash,
        raw_object_key=raw_object_key,
        metadata_json={"ocr_used": used_ocr},
    )
    session.add(version)
    await session.flush()

    masked_text, pii_detected = mask_pii(parsed.text)
    document = SourceDocument(
        workspace_id=source.workspace_id,
        source_version_id=version.id,
        title=parsed.title or source.name,
        text=masked_text,
        structure_json={
            "blocks": [
                {"type": block.block_type, "locator": block.locator} for block in parsed.blocks
            ],
            "ocr_used": used_ocr,
        },
        parse_quality_score=parsed.quality_score,
        pii_detected=pii_detected,
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
    )
    session.add(document)
    await session.flush()

    chunks = semantic_chunks(parsed.blocks)
    vectors = await embeddings.embed([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks) or any(len(vector) != 1_536 for vector in vectors):
        raise AppError("EMBEDDING_RESPONSE_INVALID", "임베딩 결과 형식이 올바르지 않습니다.", 503)
    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            SourceChunk(
                workspace_id=source.workspace_id,
                source_version_id=version.id,
                document_id=document.id,
                sequence=chunk.sequence,
                locator_json=chunk.locator,
                text=chunk.text,
                text_hash=chunk.text_hash,
                token_estimate=chunk.token_estimate,
                quality_grade=source.quality_grade,
                pii_masked=chunk.pii_masked,
                embedding=vector,
                embedding_model=embeddings.model,
                embedding_version=embeddings.version,
            )
        )
    source.current_version_id = version.id
    source.last_synced_at = datetime.now(UTC)
    source.state = (
        SourceState.NEEDS_REVIEW.value
        if parsed.quality_score < 0.6 or used_ocr
        else SourceState.READY.value
    )
    await session.flush()
    return version


async def _queue_job(
    session: AsyncSession,
    *,
    source: KnowledgeSource,
    principal: Principal,
    job_type: KnowledgeJobType,
    snapshot: str | None = None,
) -> KnowledgeJob:
    input_hash = _content_hash(snapshot or f"{source.id}:{source.updated_at.isoformat()}:{job_type.value}")
    job = KnowledgeJob(
        workspace_id=source.workspace_id,
        source_id=source.id,
        requested_by=principal.subject_id,
        job_type=job_type.value,
        state=KnowledgeJobState.QUEUED.value,
        input_snapshot_hash=input_hash,
    )
    session.add(job)
    await session.flush()
    await add_outbox_event(
        session,
        workspace_id=source.workspace_id,
        aggregate_type="knowledge_source",
        aggregate_id=str(source.id),
        event_type=f"knowledge.{job_type.value.lower()}.requested",
        schema_version="2026-08-01",
        payload={"job_id": str(job.id), "source_id": str(source.id)},
    )
    return job


async def initiate_file_upload(
    session: AsyncSession,
    *,
    principal: Principal,
    data: UploadInitiateRequest,
    storage: ObjectStorage,
    max_size: int,
) -> tuple[KnowledgeSource, UploadGrant]:
    from blogops.domain.knowledge.parsing import validate_upload_metadata

    validate_upload_metadata(data.filename, data.content_type, data.size, max_size)
    source = KnowledgeSource(
        workspace_id=principal.workspace_id,
        created_by=principal.subject_id,
        source_type=SourceType.FILE.value,
        name=data.name or data.filename,
        rights_status=data.rights_status.value,
        use_scope=data.use_scope.value,
        quality_grade=data.quality_grade.value,
        rights_confirmed_at=datetime.now(UTC) if _rights_confirmed(data.rights_status) else None,
        state=SourceState.UPLOADING.value,
        metadata_json={
            "filename": data.filename,
            "content_type": data.content_type,
            "expected_size": data.size,
        },
    )
    session.add(source)
    await session.flush()
    grant = await storage.initiate_upload(
        workspace_id=principal.workspace_id,
        source_id=source.id,
        filename=data.filename,
        content_type=data.content_type,
    )
    source.metadata_json = {**source.metadata_json, "object_key": grant.object_key}
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="knowledge.file_upload.initiated",
        target_type="knowledge_source",
        target_id=str(source.id),
        details={"size": data.size, "content_type": data.content_type},
    )
    return source, grant


async def complete_file_upload(
    session: AsyncSession,
    *,
    principal: Principal,
    source_id: UUID,
    object_key: str,
    content_hash: str,
    storage: ObjectStorage,
) -> KnowledgeJob:
    source = await get_source(session, principal.workspace_id, source_id, for_update=True)
    expected_key = source.metadata_json.get("object_key")
    if source.source_type != SourceType.FILE.value or object_key != expected_key:
        raise AppError("UPLOAD_OBJECT_MISMATCH", "업로드 대상이 일치하지 않습니다.", 409)
    object_meta = await storage.head(object_key)
    expected_size = int(source.metadata_json.get("expected_size", 0))
    if int(object_meta.get("ContentLength", -1)) != expected_size:
        raise AppError("UPLOAD_SIZE_MISMATCH", "업로드된 파일 크기가 일치하지 않습니다.", 409)
    source.state = SourceState.SCANNING.value
    source.metadata_json = {**source.metadata_json, "content_hash": content_hash}
    return await _queue_job(
        session,
        source=source,
        principal=principal,
        job_type=KnowledgeJobType.PARSE,
        snapshot=f"{object_key}:{content_hash}",
    )


async def _load_job_and_source(
    session: AsyncSession, *, workspace_id: UUID, job_id: UUID
) -> tuple[KnowledgeJob, KnowledgeSource]:
    job = await session.scalar(
        select(KnowledgeJob)
        .where(KnowledgeJob.workspace_id == workspace_id, KnowledgeJob.id == job_id)
        .with_for_update()
    )
    if job is None:
        raise AppError("KNOWLEDGE_JOB_NOT_FOUND", "지식 처리 작업을 찾을 수 없습니다.", 404)
    source = await session.scalar(
        select(KnowledgeSource)
        .where(KnowledgeSource.workspace_id == workspace_id, KnowledgeSource.id == job.source_id)
        .with_for_update()
    )
    if source is None:
        raise AppError("KNOWLEDGE_SOURCE_NOT_FOUND", "지식 소스를 찾을 수 없습니다.", 404)
    return job, source


async def _scan_content(
    scanner: MalwareScanner, *, source: KnowledgeSource, content: bytes, storage: ObjectStorage
) -> None:
    scan = await scanner.scan(content)
    if scan.status == MalwareStatus.UNAVAILABLE:
        raise AppError("MALWARE_SCANNER_UNAVAILABLE", "파일 보안 검사를 수행할 수 없습니다.", 503)
    if scan.status == MalwareStatus.INFECTED:
        object_key = source.metadata_json.get("object_key")
        if isinstance(object_key, str):
            await storage.delete(object_key)
        raise AppError("MALWARE_DETECTED", "악성 파일이 감지되어 격리 삭제되었습니다.", 422)


async def _process_file(
    session: AsyncSession,
    *,
    source: KnowledgeSource,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    embeddings: EmbeddingProvider,
    ocr: OcrProvider | None,
    max_bytes: int,
) -> SourceVersion | None:
    metadata = source.metadata_json
    object_key = metadata.get("object_key")
    filename = metadata.get("filename")
    content_type = metadata.get("content_type")
    if not all(isinstance(item, str) for item in (object_key, filename, content_type)):
        raise AppError("UPLOAD_METADATA_INVALID", "업로드 메타데이터가 올바르지 않습니다.", 422)
    content = await storage.get_bytes(str(object_key), max_bytes=max_bytes)
    await _scan_content(scanner, source=source, content=content, storage=storage)
    digest = hashlib.sha256(content).hexdigest()
    if digest != metadata.get("content_hash"):
        await storage.delete(str(object_key))
        raise AppError("UPLOAD_HASH_MISMATCH", "업로드 파일 무결성 검증에 실패했습니다.", 422)
    source.state = SourceState.PARSING.value
    try:
        parsed = parse_document(str(filename), str(content_type), content, max_bytes)
        used_ocr = False
    except AppError as exc:
        if exc.code != "DOCUMENT_TEXT_EMPTY" or ocr is None:
            if exc.code == "DOCUMENT_TEXT_EMPTY":
                source.state = SourceState.NEEDS_REVIEW.value
                return None
            raise
        text = (await ocr.extract(content, content_type=str(content_type))).strip()
        if not text:
            source.state = SourceState.NEEDS_REVIEW.value
            return None
        blocks = tuple(
            ExtractedBlock(part.strip(), {"ocr_paragraph": index})
            for index, part in enumerate(text.split("\n\n"), start=1)
            if part.strip()
        )
        parsed = ParsedDocument(
            title=source.name,
            blocks=blocks,
            parser_name="ocr",
            quality_score=0.5,
        )
        used_ocr = True
    source.state = SourceState.INDEXING.value
    return await _store_parsed_version(
        session,
        source=source,
        parsed=parsed,
        raw_object_key=str(object_key),
        content_hash=digest,
        embeddings=embeddings,
        used_ocr=used_ocr,
    )


async def _process_network_source(
    session: AsyncSession,
    *,
    source: KnowledgeSource,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    embeddings: EmbeddingProvider,
    fetcher: SafeFetcher,
    max_bytes: int,
) -> SourceVersion:
    if not source.uri:
        raise AppError("SOURCE_URL_INVALID", "소스 URL이 없습니다.", 422)
    source.state = SourceState.FETCHING.value
    fetched = await fetcher.fetch(source.uri, max_bytes=max_bytes)
    await _scan_content(scanner, source=source, content=fetched.body, storage=storage)
    digest = hashlib.sha256(fetched.body).hexdigest()
    raw_key = f"workspaces/{source.workspace_id}/knowledge/{source.id}/snapshots/{digest}"
    parsed = parse_fetched_document(
        fetched.final_url, fetched.content_type, fetched.body, max_bytes
    )
    await storage.put_bytes(raw_key, fetched.body, content_type=fetched.content_type)
    source.uri = fetched.final_url
    source.canonical_uri_hash = _content_hash(fetched.final_url)
    source.etag = fetched.etag
    if fetched.last_modified:
        try:
            source.last_modified_at = parsedate_to_datetime(fetched.last_modified)
        except (TypeError, ValueError, OverflowError):
            source.metadata_json = {
                **source.metadata_json,
                "unparsed_last_modified": fetched.last_modified,
            }
    source.state = SourceState.INDEXING.value
    return await _store_parsed_version(
        session,
        source=source,
        parsed=parsed,
        raw_object_key=raw_key,
        content_hash=digest,
        embeddings=embeddings,
        used_ocr=False,
    )


async def _delete_source_data(
    session: AsyncSession, *, source: KnowledgeSource, storage: ObjectStorage
) -> None:
    raw_keys = list(
        await session.scalars(
            select(SourceVersion.raw_object_key).where(
                SourceVersion.workspace_id == source.workspace_id,
                SourceVersion.source_id == source.id,
                SourceVersion.raw_object_key.is_not(None),
            )
        )
    )
    upload_key = source.metadata_json.get("object_key")
    if isinstance(upload_key, str):
        raw_keys.append(upload_key)
    for object_key in sorted({item for item in raw_keys if isinstance(item, str)}):
        await storage.delete(object_key)
    await session.execute(
        delete(SourceChunk).where(
            SourceChunk.workspace_id == source.workspace_id,
            SourceChunk.source_version_id.in_(
                select(SourceVersion.id).where(
                    SourceVersion.workspace_id == source.workspace_id,
                    SourceVersion.source_id == source.id,
                )
            ),
        )
    )
    await session.execute(
        delete(SourceDocument).where(
            SourceDocument.workspace_id == source.workspace_id,
            SourceDocument.source_version_id.in_(
                select(SourceVersion.id).where(
                    SourceVersion.workspace_id == source.workspace_id,
                    SourceVersion.source_id == source.id,
                )
            ),
        )
    )
    await session.execute(
        delete(SourceVersion).where(
            SourceVersion.workspace_id == source.workspace_id,
            SourceVersion.source_id == source.id,
        )
    )
    source.current_version_id = None
    source.metadata_json = {}
    source.state = SourceState.DELETED.value


async def process_knowledge_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    storage: ObjectStorage,
    scanner: MalwareScanner,
    embeddings: EmbeddingProvider,
    fetcher: SafeFetcher,
    ocr: OcrProvider | None,
    max_upload_bytes: int,
    max_fetch_bytes: int,
) -> KnowledgeJob:
    """Execute one idempotent knowledge job inside a workspace-scoped transaction."""
    job, source = await _load_job_and_source(
        session, workspace_id=workspace_id, job_id=job_id
    )
    if job.state in {
        KnowledgeJobState.SUCCEEDED.value,
        KnowledgeJobState.NEEDS_REVIEW.value,
        KnowledgeJobState.FINAL_FAILED.value,
        KnowledgeJobState.CANCELLED.value,
    }:
        return job
    if job.state not in {
        KnowledgeJobState.QUEUED.value,
        KnowledgeJobState.RETRYABLE_FAILED.value,
    }:
        raise AppError("KNOWLEDGE_JOB_STATE_CONFLICT", "처리할 수 없는 작업 상태입니다.", 409)
    job.state = KnowledgeJobState.RUNNING.value
    job.attempt += 1
    job.error_code = None
    job.error_detail = None
    try:
        version: SourceVersion | None = None
        if job.job_type == KnowledgeJobType.PARSE.value:
            version = await _process_file(
                session,
                source=source,
                storage=storage,
                scanner=scanner,
                embeddings=embeddings,
                ocr=ocr,
                max_bytes=max_upload_bytes,
            )
        elif job.job_type == KnowledgeJobType.FETCH.value:
            version = await _process_network_source(
                session,
                source=source,
                storage=storage,
                scanner=scanner,
                embeddings=embeddings,
                fetcher=fetcher,
                max_bytes=max_fetch_bytes,
            )
        elif job.job_type == KnowledgeJobType.DELETE.value:
            await _delete_source_data(session, source=source, storage=storage)
        else:
            raise AppError("KNOWLEDGE_JOB_TYPE_UNSUPPORTED", "지원하지 않는 작업 유형입니다.", 422)

        if source.state == SourceState.NEEDS_REVIEW.value:
            job.state = KnowledgeJobState.NEEDS_REVIEW.value
            job.result_json = {"source_id": str(source.id), "reason": "OCR_OR_PARSE_QUALITY"}
        else:
            job.state = KnowledgeJobState.SUCCEEDED.value
            job.result_json = {
                "source_id": str(source.id),
                "source_version_id": str(version.id) if version else None,
            }
        await append_audit_log(
            session,
            workspace_id=workspace_id,
            actor_id=job.requested_by,
            action="knowledge.job.completed",
            target_type="knowledge_job",
            target_id=str(job.id),
            details={"state": job.state, "source_id": str(source.id)},
        )
        await add_outbox_event(
            session,
            workspace_id=workspace_id,
            aggregate_type="knowledge_source",
            aggregate_id=str(source.id),
            event_type="knowledge.processing.completed",
            schema_version="2026-08-01",
            payload={"job_id": str(job.id), "state": job.state},
        )
    except AppError as exc:
        retryable = exc.status_code >= 500 and job.attempt < 3
        job.state = (
            KnowledgeJobState.RETRYABLE_FAILED.value
            if retryable
            else KnowledgeJobState.FINAL_FAILED.value
        )
        job.error_code = exc.code
        job.error_detail = exc.message[:1_000]
        source.failure_code = exc.code
        source.state = SourceState.QUEUED.value if retryable else SourceState.FAILED.value
        await append_audit_log(
            session,
            workspace_id=workspace_id,
            actor_id=job.requested_by,
            action="knowledge.job.failed",
            target_type="knowledge_job",
            target_id=str(job.id),
            details={"state": job.state, "error_code": exc.code, "attempt": job.attempt},
        )
    await session.flush()
    return job


async def get_source(
    session: AsyncSession, workspace_id: UUID, source_id: UUID, *, for_update: bool = False
) -> KnowledgeSource:
    statement: Select[tuple[KnowledgeSource]] = select(KnowledgeSource).where(
        KnowledgeSource.workspace_id == workspace_id,
        KnowledgeSource.id == source_id,
        KnowledgeSource.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    source = await session.scalar(statement)
    if source is None:
        raise AppError("KNOWLEDGE_SOURCE_NOT_FOUND", "지식 소스를 찾을 수 없습니다.", 404)
    return source


async def get_knowledge_job(
    session: AsyncSession, workspace_id: UUID, job_id: UUID
) -> KnowledgeJob:
    job = await session.scalar(
        select(KnowledgeJob).where(
            KnowledgeJob.workspace_id == workspace_id,
            KnowledgeJob.id == job_id,
        )
    )
    if job is None:
        raise AppError("KNOWLEDGE_JOB_NOT_FOUND", "지식 처리 작업을 찾을 수 없습니다.", 404)
    return job


async def list_source_versions(
    session: AsyncSession, workspace_id: UUID, source_id: UUID
) -> list[SourceVersion]:
    await get_source(session, workspace_id, source_id)
    return list(
        await session.scalars(
            select(SourceVersion)
            .where(
                SourceVersion.workspace_id == workspace_id,
                SourceVersion.source_id == source_id,
            )
            .order_by(SourceVersion.version.desc())
        )
    )


async def list_sources(
    session: AsyncSession, workspace_id: UUID, *, limit: int, cursor: UUID | None
) -> list[KnowledgeSource]:
    statement = (
        select(KnowledgeSource)
        .where(
            KnowledgeSource.workspace_id == workspace_id,
            KnowledgeSource.deleted_at.is_(None),
        )
        .order_by(KnowledgeSource.id)
        .limit(limit)
    )
    if cursor:
        statement = statement.where(KnowledgeSource.id > cursor)
    return list(await session.scalars(statement))


async def request_sync(
    session: AsyncSession, *, principal: Principal, source_id: UUID
) -> KnowledgeJob:
    source = await get_source(session, principal.workspace_id, source_id, for_update=True)
    if source.source_type in {SourceType.TEXT.value, SourceType.FAQ.value}:
        raise AppError("SOURCE_SYNC_NOT_SUPPORTED", "직접 입력 소스는 동기화할 수 없습니다.", 409)
    if source.state in {SourceState.QUEUED.value, SourceState.FETCHING.value, SourceState.PARSING.value}:
        raise AppError("SOURCE_SYNC_IN_PROGRESS", "이미 동기화 작업이 진행 중입니다.", 409)
    source.state = SourceState.QUEUED.value
    job = await _queue_job(
        session, source=source, principal=principal, job_type=KnowledgeJobType.FETCH
    )
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="knowledge.source.sync_requested",
        target_type="knowledge_source",
        target_id=str(source.id),
        details={"job_id": str(job.id)},
    )
    return job


async def request_delete(
    session: AsyncSession, *, principal: Principal, source_id: UUID
) -> KnowledgeJob:
    source = await get_source(session, principal.workspace_id, source_id, for_update=True)
    source.state = SourceState.DELETING.value
    source.deleted_at = datetime.now(UTC)
    job = await _queue_job(
        session, source=source, principal=principal, job_type=KnowledgeJobType.DELETE
    )
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="knowledge.source.deletion_requested",
        target_type="knowledge_source",
        target_id=str(source.id),
        details={"job_id": str(job.id), "includes": ["object", "documents", "chunks", "embedding"]},
    )
    return job


async def search_knowledge(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    query: str,
    limit: int,
    embeddings: EmbeddingProvider | None = None,
) -> list[SearchResult]:
    ts_query = func.websearch_to_tsquery("simple", query)
    vector = func.to_tsvector("simple", SourceChunk.text)
    rank = func.ts_rank_cd(vector, ts_query)
    query_vector = (await embeddings.embed([query]))[0] if embeddings else None
    if query_vector and any(query_vector):
        semantic_score = 1.0 - SourceChunk.embedding.cosine_distance(query_vector)
        score = (func.coalesce(rank, 0.0) * 0.35 + func.coalesce(semantic_score, 0.0) * 0.65)
        matching = vector.op("@@")(ts_query) | SourceChunk.embedding.is_not(None)
    else:
        score = rank
        matching = vector.op("@@")(ts_query)
    statement = (
        select(SourceChunk, SourceVersion.source_id, score.label("score"))
        .join(SourceVersion, SourceVersion.id == SourceChunk.source_version_id)
        .join(KnowledgeSource, KnowledgeSource.id == SourceVersion.source_id)
        .where(
            SourceChunk.workspace_id == workspace_id,
            KnowledgeSource.workspace_id == workspace_id,
            KnowledgeSource.deleted_at.is_(None),
            KnowledgeSource.state.in_(
                [SourceState.READY.value, SourceState.NEEDS_REVIEW.value]
            ),
            matching,
        )
        .order_by(score.desc(), SourceChunk.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [
        SearchResult(
            chunk_id=chunk.id,
            source_id=source_id,
            source_version_id=chunk.source_version_id,
            text=chunk.text,
            locator=chunk.locator_json,
            quality_grade=chunk.quality_grade,
            score=float(score),
        )
        for chunk, source_id, score in rows
    ]
