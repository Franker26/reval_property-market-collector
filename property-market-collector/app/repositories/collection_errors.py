"""Repositorio para collection_errors."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CollectionError


async def record(
    session: AsyncSession,
    error_type: str,
    run_id: Optional[int] = None,
    source_id: Optional[int] = None,
    listing_id: Optional[int] = None,
    external_id: Optional[str] = None,
    url: Optional[str] = None,
    error_message: Optional[str] = None,
    http_status: Optional[int] = None,
    retryable: bool = True,
) -> CollectionError:
    err = CollectionError(
        run_id=run_id,
        source_id=source_id,
        listing_id=listing_id,
        external_id=external_id,
        url=url,
        error_type=error_type,
        error_message=error_message,
        http_status=http_status,
        retryable=retryable,
    )
    session.add(err)
    await session.flush()
    return err


def classify_http_error(status_code: int) -> tuple[str, bool]:
    """Devuelve (error_type, retryable) para un status HTTP."""
    mapping = {
        403: ("http_403", False),
        404: ("http_404", False),
        429: ("http_429", True),
    }
    return mapping.get(status_code, (f"http_{status_code}", status_code >= 500))


async def count_by_type_since(session: AsyncSession, since: datetime) -> dict[str, int]:
    """Conteo de errores por tipo desde una fecha dada. Útil para health/dashboard."""
    result = await session.execute(
        select(CollectionError.error_type, func.count().label("n"))
        .where(CollectionError.failed_at >= since)
        .group_by(CollectionError.error_type)
    )
    return {row.error_type: row.n for row in result}


async def get_last(session: AsyncSession, source_id: Optional[int] = None) -> Optional[CollectionError]:
    """Devuelve el error más reciente, opcionalmente filtrado por source."""
    stmt = select(CollectionError)
    if source_id is not None:
        stmt = stmt.where(CollectionError.source_id == source_id)
    stmt = stmt.order_by(CollectionError.id.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_recent(
    session: AsyncSession,
    source_id: Optional[int] = None,
    run_id: Optional[int] = None,
    error_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[CollectionError]:
    stmt = select(CollectionError)
    if source_id is not None:
        stmt = stmt.where(CollectionError.source_id == source_id)
    if run_id is not None:
        stmt = stmt.where(CollectionError.run_id == run_id)
    if error_type is not None:
        stmt = stmt.where(CollectionError.error_type == error_type)
    stmt = stmt.order_by(CollectionError.id.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
