"""Repositorio para collection_runs."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CollectionRun


async def start(
    session: AsyncSession,
    run_type: str,
    source_id: Optional[int] = None,
    params: Optional[dict] = None,
) -> CollectionRun:
    run = CollectionRun(
        source_id=source_id,
        run_type=run_type,
        status="running",
        params_json=params,
    )
    session.add(run)
    await session.flush()
    return run


async def finish(
    session: AsyncSession,
    run_id: int,
    status: str,
    stats: Optional[dict] = None,
) -> None:
    now = datetime.utcnow()
    run = await session.get(CollectionRun, run_id)
    if run is None:
        return
    duration = (now - run.started_at.replace(tzinfo=None)).total_seconds() if run.started_at else None
    await session.execute(
        update(CollectionRun)
        .where(CollectionRun.id == run_id)
        .values(
            status=status,
            finished_at=now,
            duration_seconds=duration,
            stats_json=stats,
        )
    )


async def get_by_id(session: AsyncSession, run_id: int) -> Optional[CollectionRun]:
    return await session.get(CollectionRun, run_id)


async def list_recent(
    session: AsyncSession,
    source_id: Optional[int] = None,
    run_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[CollectionRun]:
    stmt = select(CollectionRun)
    if source_id is not None:
        stmt = stmt.where(CollectionRun.source_id == source_id)
    if run_type is not None:
        stmt = stmt.where(CollectionRun.run_type == run_type)
    stmt = stmt.order_by(CollectionRun.id.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
