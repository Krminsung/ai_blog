"""Research plan, source artifact and claim/citation ledger API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.research.schemas import (
    CitationRead,
    ClaimCreate,
    ClaimDecisionCreate,
    ClaimRead,
    ResearchArtifactCreate,
    ResearchArtifactRead,
    ResearchRunCreate,
    ResearchRunRead,
)
from blogops.domain.research.service import ResearchService
from blogops.domain.research.tasks import enqueue_research_run


router = APIRouter(tags=["research"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
ContentReader = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_READ))]
ContentWriter = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_WRITE))]
ContentApprover = Annotated[
    Principal,
    Depends(require_permissions(Permission.CONTENT_APPROVE)),
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


def research_service(session: TenantSession) -> ResearchService:
    return ResearchService(session)


Service = Annotated[ResearchService, Depends(research_service)]


@router.post(
    "/research-runs",
    response_model=ResearchRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_research_run(
    data: ResearchRunCreate,
    principal: ContentWriter,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> ResearchRunRead:
    run, created = await service.create_run(
        principal,
        data,
        idempotency_key=idempotency_key,
    )
    if created:
        background_tasks.add_task(enqueue_research_run, principal.workspace_id, run.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return ResearchRunRead.model_validate(run)


@router.get("/research-runs/{run_id}", response_model=ResearchRunRead)
async def get_research_run(
    run_id: UUID,
    principal: ContentReader,
    service: Service,
) -> ResearchRunRead:
    return ResearchRunRead.model_validate(await service.get_run(principal, run_id))


@router.get(
    "/content/{content_id}/research",
    response_model=list[ResearchRunRead],
)
async def list_content_research(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
) -> list[ResearchRunRead]:
    rows = await service.list_content_runs(principal, content_id)
    return [ResearchRunRead.model_validate(item) for item in rows]


@router.post(
    "/research-runs/{run_id}/artifacts",
    response_model=ResearchArtifactRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_research_artifact(
    run_id: UUID,
    data: ResearchArtifactCreate,
    principal: ContentWriter,
    service: Service,
) -> ResearchArtifactRead:
    return ResearchArtifactRead.model_validate(
        await service.add_artifact(principal, run_id, data)
    )


@router.get(
    "/research-runs/{run_id}/artifacts",
    response_model=list[ResearchArtifactRead],
)
async def list_research_artifacts(
    run_id: UUID,
    principal: ContentReader,
    service: Service,
    include_excluded: bool = False,
) -> list[ResearchArtifactRead]:
    rows = await service.list_artifacts(
        principal,
        run_id,
        include_excluded=include_excluded,
    )
    return [ResearchArtifactRead.model_validate(item) for item in rows]


@router.post("/research-runs/{run_id}/approve", response_model=ResearchRunRead)
async def approve_research_source_set(
    run_id: UUID,
    principal: ContentApprover,
    service: Service,
) -> ResearchRunRead:
    return ResearchRunRead.model_validate(
        await service.approve_source_set(principal, run_id)
    )


@router.post(
    "/content/{content_id}/versions/{version_id}/claims",
    response_model=ClaimRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_claim(
    content_id: UUID,
    version_id: UUID,
    data: ClaimCreate,
    principal: ContentWriter,
    service: Service,
) -> ClaimRead:
    claim, citations = await service.record_claim(
        principal,
        content_id,
        version_id,
        data,
    )
    result = ClaimRead.model_validate(claim)
    return result.model_copy(
        update={
            "citations": [CitationRead.model_validate(item) for item in citations]
        }
    )


@router.get("/content/{content_id}/claims", response_model=list[ClaimRead])
async def list_claims(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
    content_version_id: UUID | None = None,
) -> list[ClaimRead]:
    rows = await service.list_claims(
        principal,
        content_id,
        content_version_id=content_version_id,
    )
    return [
        ClaimRead.model_validate(claim).model_copy(
            update={
                "citations": [CitationRead.model_validate(item) for item in citations]
            }
        )
        for claim, citations in rows
    ]


@router.post("/claims/{claim_id}/decisions", status_code=status.HTTP_201_CREATED)
async def decide_claim(
    claim_id: UUID,
    data: ClaimDecisionCreate,
    principal: ContentWriter,
    service: Service,
) -> dict[str, str]:
    decision = await service.decide_claim(principal, claim_id, data)
    return {"id": str(decision.id), "decision": decision.decision}


@router.get("/content/{content_id}/research/export")
async def export_research(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
    content_version_id: UUID | None = None,
    format: Annotated[str, Query(pattern="^(csv|md|json)$")] = "md",
) -> Response:
    body, media_type = await service.export_claim_ledger(
        principal,
        content_id,
        content_version_id=content_version_id,
        format=format,
    )
    return Response(content=body, media_type=media_type)
