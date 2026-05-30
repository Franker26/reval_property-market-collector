"""Repositorio para zonaprop_segment_scan_queue — cola de escaneo por segmento."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ZonapropSegment, ZonapropSegmentScanQueue

_STALE_AFTER_HOURS = 6
_MAX_ATTEMPTS = 3


async def reset_stale_running(session: AsyncSession) -> int:
    """Devuelve a pending los runs que llevan más de 6h en estado running."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_STALE_AFTER_HOURS)
    result = await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(
            ZonapropSegmentScanQueue.status == "running",
            ZonapropSegmentScanQueue.locked_at < cutoff,
        )
        .values(status="pending", locked_at=None, updated_at=datetime.now(timezone.utc))
    )
    return result.rowcount  # type: ignore[return-value]


async def get_pending(session: AsyncSession, portal: str) -> list[ZonapropSegmentScanQueue]:
    """Retorna entradas pendientes para segmentos activos del portal, con segment cargado."""
    stmt = (
        select(ZonapropSegmentScanQueue)
        .options(selectinload(ZonapropSegmentScanQueue.segment))
        .join(ZonapropSegment, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
        .where(
            ZonapropSegmentScanQueue.status == "pending",
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
        )
        .order_by(ZonapropSegmentScanQueue.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_by_status(session: AsyncSession, portal: Optional[str] = None) -> dict[str, int]:
    """Devuelve conteo de entradas agrupadas por status. Útil para health/dashboard."""
    stmt = (
        select(ZonapropSegmentScanQueue.status, func.count().label("n"))
        .join(ZonapropSegment, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
    )
    if portal:
        stmt = stmt.where(ZonapropSegment.portal == portal)
    stmt = stmt.group_by(ZonapropSegmentScanQueue.status)
    result = await session.execute(stmt)
    return {row.status: row.n for row in result}


async def mark_started(session: AsyncSession, run_id: int) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(status="running", locked_at=now, started_at=now, updated_at=now)
    )


async def mark_complete(
    session: AsyncSession,
    run_id: int,
    pages_scanned: int = 0,
    listings_found: int = 0,
    new_count: int = 0,
    changed_count: int = 0,
    requests_total: int = 0,
    requests_success: int = 0,
    requests_failed: int = 0,
    requests_403: int = 0,
    requests_429: int = 0,
    requests_5xx: int = 0,
    timeouts: int = 0,
    avg_latency_ms: Optional[float] = None,
    max_latency_ms: Optional[float] = None,
    cooldown_triggered: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(
            status="complete",
            completed_at=now,
            pages_scanned=pages_scanned,
            listings_found=listings_found,
            new_count=new_count,
            changed_count=changed_count,
            requests_total=requests_total,
            requests_success=requests_success,
            requests_failed=requests_failed,
            requests_403=requests_403,
            requests_429=requests_429,
            requests_5xx=requests_5xx,
            timeouts=timeouts,
            avg_latency_ms=avg_latency_ms,
            max_latency_ms=max_latency_ms,
            cooldown_triggered=cooldown_triggered,
            updated_at=now,
        )
    )


async def mark_pending(
    session: AsyncSession,
    run_id: int,
    last_error: Optional[str] = None,
) -> None:
    values: dict = {"status": "pending", "locked_at": None, "updated_at": datetime.now(timezone.utc)}
    if last_error is not None:
        values["last_error"] = last_error
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(**values)
    )


async def mark_failed(session: AsyncSession, run_id: int, error: str) -> None:
    result = await session.execute(
        select(ZonapropSegmentScanQueue.attempt_count)
        .where(ZonapropSegmentScanQueue.id == run_id)
    )
    attempt_count = (result.scalar_one_or_none() or 0) + 1
    new_status = "failed" if attempt_count >= _MAX_ATTEMPTS else "pending"
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(
            status=new_status,
            attempt_count=attempt_count,
            last_error=error,
            locked_at=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
