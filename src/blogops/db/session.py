"""Async PostgreSQL engine and tenant-scoped session helpers."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.permissions import get_principal


class Database:
    def __init__(self) -> None:
        settings = get_settings()
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            connect_args={
                "server_settings": {
                    "application_name": settings.otel_service_name,
                    "statement_timeout": str(settings.database_statement_timeout_ms),
                    "timezone": "UTC",
                }
            },
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()


@lru_cache(maxsize=1)
def get_database() -> Database:
    return Database()


async def get_session() -> AsyncIterator[AsyncSession]:
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            yield session


async def apply_workspace_scope(session: AsyncSession, workspace_id: UUID) -> None:
    """Bind a verified workspace to the current database transaction for RLS."""
    await session.execute(
        text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
        {"workspace_id": str(workspace_id)},
    )


async def get_tenant_session(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AsyncIterator[AsyncSession]:
    """Open a transaction whose PostgreSQL RLS context matches the verified principal."""
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, principal.workspace_id)
            yield session
