"""Repositorio para listing_location_normalization."""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingEntity
from app.db.models.location_normalization import ListingLocationNormalization

_UPDATE_KEYS = frozenset({
    "raw_province", "raw_city", "raw_neighborhood", "raw_address",
    "raw_latitude", "raw_longitude",
    "normalized_country", "normalized_province", "normalized_city",
    "normalized_neighborhood", "normalized_address",
    "normalized_latitude", "normalized_longitude",
    "geo_provider", "geo_provider_place_id", "geo_confidence",
    "geo_status", "geo_error",
})


def compute_location(entity: ListingEntity) -> dict:
    has_coords = entity.lat is not None and entity.lon is not None
    if has_coords:
        return {
            "listing_id":              entity.id,
            "raw_province":            entity.province_name,
            "raw_city":                entity.city,
            "raw_neighborhood":        entity.neighborhood,
            "raw_address":             entity.address,
            "raw_latitude":            entity.lat,
            "raw_longitude":           entity.lon,
            "normalized_country":      "AR",
            "normalized_province":     entity.province_name,
            "normalized_city":         entity.city,
            "normalized_neighborhood": entity.neighborhood,
            "normalized_address":      entity.address,
            "normalized_latitude":     entity.lat,
            "normalized_longitude":    entity.lon,
            "geo_provider":            "portal",
            "geo_provider_place_id":   None,
            "geo_confidence":          "high",
            "geo_status":              "coordinates",
            "geo_error":               None,
        }
    return {
        "listing_id":              entity.id,
        "raw_province":            entity.province_name,
        "raw_city":                entity.city,
        "raw_neighborhood":        entity.neighborhood,
        "raw_address":             entity.address,
        "raw_latitude":            None,
        "raw_longitude":           None,
        "normalized_country":      None,
        "normalized_province":     entity.province_name,
        "normalized_city":         entity.city,
        "normalized_neighborhood": entity.neighborhood,
        "normalized_address":      None,
        "normalized_latitude":     None,
        "normalized_longitude":    None,
        "geo_provider":            None,
        "geo_provider_place_id":   None,
        "geo_confidence":          None,
        "geo_status":              "pending",
        "geo_error":               None,
    }


async def upsert_rows(session: AsyncSession, rows: list[dict]) -> None:
    """Upsert pre-computed location dicts. Caller must commit the session."""
    if not rows:
        return
    stmt = pg_insert(ListingLocationNormalization).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["listing_id"],
        set_={
            **{k: stmt.excluded[k] for k in _UPDATE_KEYS},
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def upsert_batch(session: AsyncSession, entities: list[ListingEntity]) -> None:
    """Compute location from attached entities and upsert. Caller must commit the session."""
    await upsert_rows(session, [compute_location(e) for e in entities])
