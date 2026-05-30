"""Repositorio para listing_entities."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import compute_listing_hash
from app.db.models import ListingEntity

# Campos de payload actualizables en CASO C (excluye identidad)
_PAYLOAD_KEYS = (
    "canonical_url", "operation_type", "property_type",
    "status", "source_modified_at",
    "price_amount", "price_currency", "expenses_amount", "expenses_currency",
    "surface_total", "surface_covered",
    "rooms", "bedrooms", "bathrooms", "garages",
    "address", "lat", "lon", "neighborhood", "city", "province_name",
    "seller_id", "seller_name", "seller_type",
    "extra_data",
)


async def upsert_batch(
    session: AsyncSession,
    source_id: int,
    postings: list[dict],
) -> list[tuple[ListingEntity, bool]]:
    """
    Procesa un lote de postings del discovery API.

    Implementa tres casos:
      A — nuevo listing: INSERT con todos los campos + snapshot requerido
      B — existente sin cambios: UPDATE last_seen_at solamente
      C — existente con cambios: UPDATE todos los campos de payload + snapshot requerido

    Devuelve lista de (entity, changed) donde changed=True implica crear snapshot.
    """
    if not postings:
        return []

    external_ids = [p["external_id"] for p in postings]
    existing_map = await _get_many(session, source_id, external_ids)

    now = datetime.utcnow()
    results: list[tuple[ListingEntity, bool]] = []

    for posting in postings:
        new_hash = compute_listing_hash(posting)
        existing = existing_map.get(posting["external_id"])

        if existing is None:
            # CASO A: listing nuevo
            entity = ListingEntity(
                source_id=source_id,
                external_id=posting["external_id"],
                content_hash=new_hash,
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                **{k: posting.get(k) for k in _PAYLOAD_KEYS},
            )
            session.add(entity)
            results.append((entity, True))

        elif existing.content_hash == new_hash:
            # CASO B: sin cambios — solo tocar last_seen_at
            existing.last_seen_at = now
            results.append((existing, False))

        else:
            # CASO C: algo cambió — actualizar payload completo
            for key in _PAYLOAD_KEYS:
                setattr(existing, key, posting.get(key))
            existing.content_hash = new_hash
            existing.last_seen_at = now
            existing.last_changed_at = now
            results.append((existing, True))

    await session.flush()
    return results



def _entity_as_dict(entity: ListingEntity) -> dict:
    return {k: getattr(entity, k, None) for k in _PAYLOAD_KEYS}


async def _get_many(
    session: AsyncSession,
    source_id: int,
    external_ids: list[str],
) -> dict[str, ListingEntity]:
    result = await session.execute(
        select(ListingEntity).where(
            ListingEntity.source_id == source_id,
            ListingEntity.external_id.in_(external_ids),
        )
    )
    return {e.external_id: e for e in result.scalars().all()}


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
