"""Repositorio para listing_entities."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingEntity


async def upsert(
    session: AsyncSession,
    source_id: int,
    external_id: str,
    canonical_url: Optional[str] = None,
    operation_type: Optional[str] = None,
    property_type: Optional[str] = None,
    status: str = "unknown",
) -> ListingEntity:
    """
    Crea o actualiza un listing_entity usando source_id + external_id como clave.
    Devuelve la entidad resultante.
    """
    stmt = (
        insert(ListingEntity)
        .values(
            source_id=source_id,
            external_id=external_id,
            canonical_url=canonical_url,
            operation_type=operation_type,
            property_type=property_type,
            status=status,
        )
        .on_conflict_do_update(
            constraint="uq_listing_source_external",
            set_={
                "canonical_url": canonical_url,
                "last_seen_at": datetime.utcnow(),
            },
        )
        .returning(ListingEntity)
    )
    result = await session.execute(stmt)
    await session.flush()
    row = result.fetchone()
    if row:
        return row[0]
    # Fallback: buscar el existente
    existing = await get_by_source_and_external(session, source_id, external_id)
    assert existing is not None
    return existing


async def get_by_source_and_external(
    session: AsyncSession, source_id: int, external_id: str
) -> Optional[ListingEntity]:
    result = await session.execute(
        select(ListingEntity).where(
            ListingEntity.source_id == source_id,
            ListingEntity.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, listing_id: int) -> Optional[ListingEntity]:
    return await session.get(ListingEntity, listing_id)


async def mark_success(
    session: AsyncSession,
    listing_id: int,
    snapshot_id: int,
    status: str = "active",
    changed: bool = False,
) -> None:
    now = datetime.utcnow()
    values: dict = {
        "last_success_at": now,
        "last_seen_at": now,
        "last_snapshot_id": snapshot_id,
        "status": status,
    }
    if changed:
        values["last_changed_at"] = now
    await session.execute(
        update(ListingEntity).where(ListingEntity.id == listing_id).values(**values)
    )


async def mark_error(
    session: AsyncSession,
    listing_id: int,
    status: Optional[str] = None,
) -> None:
    now = datetime.utcnow()
    values: dict = {"last_error_at": now}
    if status:
        values["status"] = status
    await session.execute(
        update(ListingEntity).where(ListingEntity.id == listing_id).values(**values)
    )


async def list_active(
    session: AsyncSession,
    source_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ListingEntity]:
    stmt = select(ListingEntity).where(ListingEntity.status == "active")
    if source_id is not None:
        stmt = stmt.where(ListingEntity.source_id == source_id)
    stmt = stmt.order_by(ListingEntity.id).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_all(
    session: AsyncSession,
    source_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ListingEntity]:
    stmt = select(ListingEntity)
    if source_id is not None:
        stmt = stmt.where(ListingEntity.source_id == source_id)
    if status is not None:
        stmt = stmt.where(ListingEntity.status == status)
    stmt = stmt.order_by(ListingEntity.id.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
