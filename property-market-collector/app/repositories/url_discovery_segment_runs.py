"""Repositorio para url_discovery_segment_runs — progress tracking por segmento."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketSegment, UrlDiscoverySegmentRun

_STALE_AFTER_HOURS = 6
_MAX_ATTEMPTS = 3


async def reset_stale_running(session: AsyncSession) -> int:
    """Devuelve a pending los runs que llevan más de 6h en estado running."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_STALE_AFTER_HOURS)
    result = await session.execute(
        update(UrlDiscoverySegmentRun)
        .where(
            UrlDiscoverySegmentRun.status == "running",
            UrlDiscoverySegmentRun.locked_at < cutoff,
        )
        .values(status="pending", locked_at=None, updated_at=datetime.now(timezone.utc))
    )
    return result.rowcount  # type: ignore[return-value]


async def get_pending(session: AsyncSession, portal: str) -> list[UrlDiscoverySegmentRun]:
    """Retorna runs pendientes para segmentos activos del portal, con segment cargado."""
    stmt = (
        select(UrlDiscoverySegmentRun)
        .options(selectinload(UrlDiscoverySegmentRun.segment))
        .join(MarketSegment, UrlDiscoverySegmentRun.segment_id == MarketSegment.id)
        .where(
            UrlDiscoverySegmentRun.status == "pending",
            MarketSegment.portal == portal,
            MarketSegment.status == "active",
        )
        .order_by(UrlDiscoverySegmentRun.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_started(session: AsyncSession, run_id: int) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(UrlDiscoverySegmentRun)
        .where(UrlDiscoverySegmentRun.id == run_id)
        .values(status="running", locked_at=now, started_at=now, updated_at=now)
    )


async def mark_complete(
    session: AsyncSession,
    run_id: int,
    pages_scanned: int = 0,
    listings_found: int = 0,
    new_count: int = 0,
    changed_count: int = 0,
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(UrlDiscoverySegmentRun)
        .where(UrlDiscoverySegmentRun.id == run_id)
        .values(
            status="complete",
            completed_at=now,
            pages_scanned=pages_scanned,
            listings_found=listings_found,
            new_count=new_count,
            changed_count=changed_count,
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
        update(UrlDiscoverySegmentRun)
        .where(UrlDiscoverySegmentRun.id == run_id)
        .values(**values)
    )


async def mark_failed(session: AsyncSession, run_id: int, error: str) -> None:
    result = await session.execute(
        select(UrlDiscoverySegmentRun.attempt_count)
        .where(UrlDiscoverySegmentRun.id == run_id)
    )
    attempt_count = (result.scalar_one_or_none() or 0) + 1
    new_status = "failed" if attempt_count >= _MAX_ATTEMPTS else "pending"
    await session.execute(
        update(UrlDiscoverySegmentRun)
        .where(UrlDiscoverySegmentRun.id == run_id)
        .values(
            status=new_status,
            attempt_count=attempt_count,
            last_error=error,
            locked_at=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
