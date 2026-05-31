"""Repositorio para listing_snapshots."""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import compute_listing_hash
from app.db.models import ListingSnapshot

if TYPE_CHECKING:
    from app.db.models import ListingEntity

# Campos de payload copiados del posting/entity al snapshot
_SNAPSHOT_PAYLOAD_KEYS = (
    "status", "source_modified_at",
    "price_amount", "price_currency", "expenses_amount", "expenses_currency",
    "surface_total", "surface_covered",
    "rooms", "bedrooms", "bathrooms", "garages",
    "address", "lat", "lon", "neighborhood", "city", "province_name",
    "seller_id", "seller_name", "seller_type",
    "generated_title", "description", "toilettes", "antiquity_years", "disposition", "orientation",
    "extra_data",
)


async def create_from_posting(
    session: AsyncSession,
    listing_id: int,
    posting: dict,
    content_hash: str,
) -> ListingSnapshot:
    """Crea un snapshot a partir de un posting dict del adapter."""
    snapshot = ListingSnapshot(
        listing_id=listing_id,
        content_hash=content_hash,
        **{k: posting.get(k) for k in _SNAPSHOT_PAYLOAD_KEYS},
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def create_from_entity(
    session: AsyncSession,
    entity: "ListingEntity",
) -> ListingSnapshot:
    """Crea un snapshot copiando el estado actual de un ListingEntity."""
    entity_dict = {k: getattr(entity, k, None) for k in _SNAPSHOT_PAYLOAD_KEYS}
    content_hash = entity.content_hash or compute_listing_hash(entity_dict)
    snapshot = ListingSnapshot(
        listing_id=entity.id,
        content_hash=content_hash,
        **entity_dict,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def get_latest(
    session: AsyncSession, listing_id: int
) -> Optional[ListingSnapshot]:
    result = await session.execute(
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id == listing_id)
        .order_by(ListingSnapshot.captured_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_for_listing(
    session: AsyncSession,
    listing_id: int,
    limit: int = 20,
    offset: int = 0,
) -> list[ListingSnapshot]:
    result = await session.execute(
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id == listing_id)
        .order_by(ListingSnapshot.captured_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
