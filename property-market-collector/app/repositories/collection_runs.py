"""Repositorio para collection_runs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CollectionRun


async def start(
    session: AsyncSession,
    run_type: str,
    source_id: Optional[int] = None,
    params: Optional[dict] = None,
    mode: str = "manual",
) -> CollectionRun:
    run = CollectionRun(
        source_id=source_id,
        run_type=run_type,
        mode=mode,
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
    now = datetime.now(timezone.utc)
    run = await session.get(CollectionRun, run_id)
    if run is None:
        return
    started = run.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    duration = (now - started).total_seconds() if started else None
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


async def reset_stale_running_runs(session: AsyncSession) -> int:
    """Marca como 'failed' los collection_runs que quedaron activos al reiniciar."""
    result = await session.execute(
        update(CollectionRun)
        .where(CollectionRun.status.in_(["running", "stopping", "force_stopping"]))
        .values(status="failed", finished_at=datetime.now(timezone.utc))
    )
    return result.rowcount  # type: ignore[return-value]


async def get_by_id(session: AsyncSession, run_id: int) -> Optional[CollectionRun]:
    return await session.get(CollectionRun, run_id)


async def update_status(session: AsyncSession, run_id: int, status: str) -> None:
    """Actualiza el status de un run sin cerrar el run (sin finished_at)."""
    await session.execute(
        update(CollectionRun)
        .where(CollectionRun.id == run_id)
        .values(status=status)
    )


async def get_active(session: AsyncSession) -> Optional[CollectionRun]:
    """Devuelve el run activo (running/stopping/force_stopping), si existe."""
    result = await session.execute(
        select(CollectionRun)
        .where(CollectionRun.status.in_(["running", "stopping", "force_stopping"]))
        .order_by(CollectionRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_last_completed(session: AsyncSession, run_type: Optional[str] = None) -> Optional[CollectionRun]:
    """Devuelve el último run completado (success o failed), opcionalmente filtrado por tipo."""
    stmt = (
        select(CollectionRun)
        .where(CollectionRun.status.in_(["success", "failed", "partial"]))
    )
    if run_type:
        stmt = stmt.where(CollectionRun.run_type == run_type)
    stmt = stmt.order_by(CollectionRun.id.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


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
