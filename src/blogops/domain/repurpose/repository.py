"""Workspace-scoped repository for repurposing roots and immutable results."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.repurpose.models import (
    ChannelTemplate,
    ChannelTemplateVersion,
    RepurposeApproval,
    RepurposeDeliveryRequest,
    RepurposeExportArtifact,
    RepurposeJob,
    RepurposeJobCommand,
    RepurposeJobItem,
    RepurposeVariant,
)


class RepurposeRepository:
    def __init__(self, session: AsyncSession, workspace_id: UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def template(self, row_id: UUID, *, lock: bool = False) -> ChannelTemplate:
        return await self._required(ChannelTemplate, row_id, "REPURPOSE_TEMPLATE", lock)

    async def template_version(self, row_id: UUID) -> ChannelTemplateVersion:
        return await self._required(
            ChannelTemplateVersion, row_id, "REPURPOSE_TEMPLATE_VERSION"
        )

    async def templates(self) -> list[ChannelTemplate]:
        return list(
            await self.session.scalars(
                select(ChannelTemplate)
                .where(ChannelTemplate.workspace_id == self.workspace_id)
                .order_by(ChannelTemplate.channel, ChannelTemplate.name)
            )
        )

    async def idempotent_job(
        self, actor_id: UUID, operation: str, idempotency_key: str
    ) -> RepurposeJob | None:
        return await self.session.scalar(
            select(RepurposeJob).where(
                RepurposeJob.workspace_id == self.workspace_id,
                RepurposeJob.requested_by == actor_id,
                RepurposeJob.operation == operation,
                RepurposeJob.idempotency_key == idempotency_key,
            )
        )

    async def job(self, row_id: UUID, *, lock: bool = False) -> RepurposeJob:
        return await self._required(RepurposeJob, row_id, "REPURPOSE_JOB", lock)

    async def job_items(self, job_id: UUID, *, lock: bool = False) -> list[RepurposeJobItem]:
        statement = (
            select(RepurposeJobItem)
            .where(
                RepurposeJobItem.workspace_id == self.workspace_id,
                RepurposeJobItem.job_id == job_id,
            )
            .order_by(RepurposeJobItem.position, RepurposeJobItem.id)
        )
        if lock:
            statement = statement.with_for_update(skip_locked=True)
        return list(await self.session.scalars(statement))

    async def item(self, row_id: UUID, *, lock: bool = False) -> RepurposeJobItem:
        return await self._required(RepurposeJobItem, row_id, "REPURPOSE_ITEM", lock)

    async def variant(self, row_id: UUID) -> RepurposeVariant:
        return await self._required(RepurposeVariant, row_id, "REPURPOSE_VARIANT")

    async def variants(self, job_id: UUID) -> list[RepurposeVariant]:
        item_ids = select(RepurposeJobItem.id).where(
            RepurposeJobItem.workspace_id == self.workspace_id,
            RepurposeJobItem.job_id == job_id,
        )
        return list(
            await self.session.scalars(
                select(RepurposeVariant)
                .where(
                    RepurposeVariant.workspace_id == self.workspace_id,
                    RepurposeVariant.job_item_id.in_(item_ids),
                )
                .order_by(RepurposeVariant.job_item_id, RepurposeVariant.variant_no)
            )
        )

    async def approval(self, row_id: UUID) -> RepurposeApproval:
        return await self._required(RepurposeApproval, row_id, "REPURPOSE_APPROVAL")

    async def latest_approval(self, variant_id: UUID) -> RepurposeApproval | None:
        return await self.session.scalar(
            select(RepurposeApproval)
            .where(
                RepurposeApproval.workspace_id == self.workspace_id,
                RepurposeApproval.variant_id == variant_id,
            )
            .order_by(RepurposeApproval.created_at.desc(), RepurposeApproval.id.desc())
            .limit(1)
        )

    async def export(self, row_id: UUID) -> RepurposeExportArtifact:
        return await self._required(RepurposeExportArtifact, row_id, "REPURPOSE_EXPORT")

    async def delivery(self, row_id: UUID) -> RepurposeDeliveryRequest:
        return await self._required(RepurposeDeliveryRequest, row_id, "REPURPOSE_DELIVERY")

    async def idempotent_delivery(
        self, actor_id: UUID, idempotency_key: str
    ) -> RepurposeDeliveryRequest | None:
        return await self.session.scalar(
            select(RepurposeDeliveryRequest).where(
                RepurposeDeliveryRequest.workspace_id == self.workspace_id,
                RepurposeDeliveryRequest.requested_by == actor_id,
                RepurposeDeliveryRequest.idempotency_key == idempotency_key,
            )
        )

    async def idempotent_command(
        self,
        job_id: UUID,
        actor_id: UUID,
        command: str,
        idempotency_key: str,
    ) -> RepurposeJobCommand | None:
        return await self.session.scalar(
            select(RepurposeJobCommand).where(
                RepurposeJobCommand.workspace_id == self.workspace_id,
                RepurposeJobCommand.job_id == job_id,
                RepurposeJobCommand.actor_id == actor_id,
                RepurposeJobCommand.command == command,
                RepurposeJobCommand.idempotency_key == idempotency_key,
            )
        )

    async def _required(
        self, model: type, row_id: UUID, code: str, lock: bool = False
    ) -> object:
        statement: Select = select(model).where(
            model.workspace_id == self.workspace_id,
            model.id == row_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise AppError(
                code=f"{code}_NOT_FOUND",
                message="현재 워크스페이스에서 요청한 리퍼포징 리소스를 찾을 수 없습니다.",
                status_code=404,
            )
        return row
