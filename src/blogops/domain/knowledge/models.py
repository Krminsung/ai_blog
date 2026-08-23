"""Knowledge source lineage models with an explicit workspace key on every row."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.knowledge.enums import (
    KnowledgeJobState,
    KnowledgeJobType,
    RightsStatus,
    SourceQualityGrade,
    SourceState,
    SourceType,
    UseScope,
)


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        Index("ix_knowledge_sources_workspace_state", "workspace_id", "state"),
        UniqueConstraint("workspace_id", "id", name="knowledge_source_workspace_id"),
        UniqueConstraint("workspace_id", "canonical_uri_hash", name="knowledge_source_uri_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["source_versions.workspace_id", "source_versions.id"],
            name="fk_knowledge_sources_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    created_by: Mapped[UUID] = mapped_column(index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    uri: Mapped[str | None] = mapped_column(Text)
    canonical_uri_hash: Mapped[str | None] = mapped_column(String(64))
    rights_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RightsStatus.UNCONFIRMED.value
    )
    use_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UseScope.INTERNAL_ONLY.value
    )
    quality_grade: Mapped[str] = mapped_column(
        String(1), nullable=False, default=SourceQualityGrade.D.value
    )
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=SourceState.CREATED.value)
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["knowledge_sources.workspace_id", "knowledge_sources.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="source_version_workspace_id"),
        UniqueConstraint("workspace_id", "source_id", "version", name="source_version_number"),
        UniqueConstraint("workspace_id", "source_id", "content_hash", name="source_version_hash"),
    )

    workspace_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    source_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_object_key: Mapped[str | None] = mapped_column(Text)
    source_etag: Mapped[str | None] = mapped_column(String(512))
    source_last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SourceDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_version_id"],
            ["source_versions.workspace_id", "source_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "source_version_id", "id", name="source_document_lineage_id"
        ),
        Index("ix_source_documents_workspace_version", "workspace_id", "source_version_id"),
        Index("ix_source_documents_search", "search_vector", postgresql_using="gin"),
    )

    workspace_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    source_version_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ko")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    structure_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    search_vector: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True),
    )
    parse_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    pii_detected: Mapped[bool] = mapped_column(nullable=False, default=False)
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_version_id", "document_id"],
            [
                "source_documents.workspace_id",
                "source_documents.source_version_id",
                "source_documents.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "document_id", "sequence", name="source_chunk_sequence"),
        Index("ix_source_chunks_workspace_hash", "workspace_id", "text_hash"),
        Index(
            "ix_source_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    source_version_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    document_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    locator_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_grade: Mapped[str] = mapped_column(String(1), nullable=False)
    pii_masked: Mapped[bool] = mapped_column(nullable=False, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["knowledge_sources.workspace_id", "knowledge_sources.id"],
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_jobs_workspace_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    source_id: Mapped[UUID] = mapped_column(index=True, nullable=False)
    requested_by: Mapped[UUID] = mapped_column(index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=KnowledgeJobType.PARSE.value
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=KnowledgeJobState.QUEUED.value
    )
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
