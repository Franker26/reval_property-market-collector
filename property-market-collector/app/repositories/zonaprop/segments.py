"""Repositorio para zonaprop_segments y zonaprop_segment_snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.models import ZonapropSegment, ZonapropSegmentSnapshot, ZonapropSegmentScanQueue


async def upsert_segment(
    session: AsyncSession,
    portal: str,
    operation_key: str,
    operation_value: int,
    province_key: str,
    province_value: int,
    price_min: float,
    price_max: float,
    surface_min: float,
    surface_max: float,
    total_count: Optional[int],
    depth: int,
    parent_id: Optional[int],
    is_leaf: bool,
    is_oversized: bool,
) -> ZonapropSegment:
    """Upsert idempotente por boundaries. Reactiva segmentos existentes; crea nuevos si no existen."""
    stmt = (
        insert(ZonapropSegment)
        .values(
            portal=portal,
            operation_key=operation_key,
            operation_value=operation_value,
            province_key=province_key,
            province_value=province_value,
            price_min=price_min,
            price_max=price_max,
            surface_min=surface_min,
            surface_max=surface_max,
            total_count=total_count,
            depth=depth,
            parent_id=parent_id,
            is_leaf=is_leaf,
            is_oversized=is_oversized,
            status="active",
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_zonaprop_segments_boundaries",
        set_={
            "status": "active",
            "total_count": stmt.excluded.total_count,
            "depth": stmt.excluded.depth,
            "parent_id": stmt.excluded.parent_id,
            "is_leaf": stmt.excluded.is_leaf,
            "is_oversized": stmt.excluded.is_oversized,
            "last_checked_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(ZonapropSegment.id)

    result = await session.execute(stmt)
    segment_id = result.scalar_one()
    segment = await session.get(ZonapropSegment, segment_id)
    return segment  # type: ignore[return-value]


async def sync_pending_scan_queue(session: AsyncSession, portal: str) -> int:
    """Inserta entradas pendientes en la cola de scan para leaf segments activos. Idempotente."""
    subq = (
        select(ZonapropSegment.id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.is_leaf == True,  # noqa: E712
            ZonapropSegment.status == "active",
        )
    )
    stmt = (
        insert(ZonapropSegmentScanQueue)
        .from_select(["segment_id"], subq)
        .on_conflict_do_nothing(index_elements=["segment_id"])
    )
    result = await session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]


async def get_leaf_segments(
    session: AsyncSession,
    portal: str,
    operation_key: Optional[str] = None,
    province_key: Optional[str] = None,
) -> list[ZonapropSegment]:
    stmt = select(ZonapropSegment).where(
        ZonapropSegment.portal == portal,
        ZonapropSegment.is_leaf == True,  # noqa: E712
        ZonapropSegment.status == "active",
    )
    if operation_key:
        stmt = stmt.where(ZonapropSegment.operation_key == operation_key)
    if province_key:
        stmt = stmt.where(ZonapropSegment.province_key == province_key)
    stmt = stmt.order_by(ZonapropSegment.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, segment_id: int) -> Optional[ZonapropSegment]:
    return await session.get(ZonapropSegment, segment_id)


async def save_snapshot(
    session: AsyncSession,
    segment_id: int,
    total_count: int,
    price_min: float,
    price_max: float,
    surface_min: float,
    surface_max: float,
) -> ZonapropSegmentSnapshot:
    snapshot = ZonapropSegmentSnapshot(
        segment_id=segment_id,
        total_count=total_count,
        price_min=price_min,
        price_max=price_max,
        surface_min=surface_min,
        surface_max=surface_max,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def update_total_count(
    session: AsyncSession,
    segment_id: int,
    total_count: int,
) -> None:
    await session.execute(
        update(ZonapropSegment)
        .where(ZonapropSegment.id == segment_id)
        .values(total_count=total_count, last_checked_at=datetime.now(timezone.utc))
    )


async def deactivate_portal_segments(session: AsyncSession, portal: str) -> int:
    """Marca como inactivos todos los segmentos de un portal (pre-rebuild)."""
    result = await session.execute(
        update(ZonapropSegment)
        .where(ZonapropSegment.portal == portal, ZonapropSegment.status == "active")
        .values(status="inactive")
    )
    return result.rowcount  # type: ignore[return-value]
