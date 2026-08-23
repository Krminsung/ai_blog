"""Shared dependency wiring for API-key management and authentication."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.db.session import get_session
from blogops.domain.developer.providers import FailClosedDeveloperAdapters
from blogops.domain.developer.service import DeveloperService


def get_developer_service(
    session: AsyncSession = Depends(get_session),
) -> DeveloperService:
    return DeveloperService(session)


def get_developer_adapters() -> FailClosedDeveloperAdapters:
    """Production overrides this single boundary with managed runtime adapters."""

    return FailClosedDeveloperAdapters()
