"""PostgreSQL transaction-scoped advisory lock helpers."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.serialization import canonical_json_hash


def creation_guard_key(namespace: str, *identity: object) -> str:
    """Build an unambiguous, stable key for a creation transaction lock."""

    digest = canonical_json_hash(
        {"namespace": namespace, "identity": list(identity)}
    )
    return f"blogops:stage9:create:{namespace}:{digest}"


async def acquire_transaction_advisory_lock(
    session: AsyncSession,
    guard_key: str,
) -> None:
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:guard_key, 0))"
        ),
        {"guard_key": guard_key},
    )


async def acquire_creation_guard(
    session: AsyncSession,
    namespace: str,
    *identity: object,
) -> None:
    await acquire_transaction_advisory_lock(
        session,
        creation_guard_key(namespace, *identity),
    )
