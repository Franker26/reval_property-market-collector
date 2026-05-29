"""Repositorio para listing_snapshots."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import (
    compute_availability_hash,
    compute_content_hash,
    compute_location_hash,
    compute_media_hash,
    compute_price_hash,
)
from app.db.models import ListingSnapshot


async def create(
    session: AsyncSession,
    listing_id: int,
    payload: dict,
    raw_payload: Optional[dict] = None,
) -> ListingSnapshot:
    """
    Persiste un snapshot, calculando todos los hashes.
    Devuelve el snapshot creado.
    """
    content_hash = compute_content_hash(payload)
    price_hash = compute_price_hash(payload)
    availability_hash = compute_availability_hash(payload)
    location_hash = compute_location_hash(payload)
    media_hash = compute_media_hash(payload)

    price = payload.get("price") or {}
    prop = payload.get("property") or payload.get("property_info") or {}

    snapshot = ListingSnapshot(
        listing_id=listing_id,
        payload_json=payload,
        raw_payload_json=raw_payload,
        content_hash=content_hash,
        price_hash=price_hash,
        availability_hash=availability_hash,
        location_hash=location_hash,
        media_hash=media_hash,
        price_amount=price.get("precio"),
        price_currency=price.get("currency"),
        expenses_amount=price.get("expenses"),
        surface_total=prop.get("superficie_total"),
        surface_covered=prop.get("superficie_cubierta"),
        rooms=prop.get("ambientes"),
        bedrooms=prop.get("bedrooms"),
        bathrooms=prop.get("bathrooms"),
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


async def has_changed(
    session: AsyncSession, listing_id: int, new_content_hash: str
) -> bool:
    """True si el content_hash del último snapshot es distinto al nuevo."""
    latest = await get_latest(session, listing_id)
    if latest is None:
        return True
    return latest.content_hash != new_content_hash


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
