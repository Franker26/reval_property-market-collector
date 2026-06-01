"""Repositorio para listing_market_facts."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market_facts import ListingMarketFacts

# Columnas que se actualizan en ON CONFLICT (excluye identidad y created_at)
_UPDATE_KEYS = frozenset({
    "source_id", "external_id", "status", "operation_type", "property_type",
    "price_usd", "price_currency", "surface_total", "surface_covered",
    "price_per_m2_total", "price_per_m2_covered",
    "first_seen_at", "last_seen_at",
    "days_observed", "days_on_market",
    "initial_price_usd", "current_price_usd", "min_price_usd", "max_price_usd",
    "price_change_count", "last_price_change_at", "price_delta_usd", "price_delta_pct",
    "has_price", "has_surface", "has_location", "has_seller", "data_quality_score",
    "province", "city", "neighborhood", "latitude", "longitude",
    "geo_cell_id", "location_source", "market_bucket",
    "last_snapshot_at", "snapshot_count",
})


async def upsert_facts_batch(session: AsyncSession, facts_list: list[dict]) -> int:
    """
    INSERT ... ON CONFLICT (listing_id) DO UPDATE SET ...
    Retorna cantidad de filas en el batch (no el rowcount real de PG).
    """
    if not facts_list:
        return 0

    stmt = pg_insert(ListingMarketFacts).values(facts_list)
    update_cols = {k: stmt.excluded[k] for k in _UPDATE_KEYS if k in facts_list[0]}
    stmt = stmt.on_conflict_do_update(
        index_elements=["listing_id"],
        set_=update_cols,
    )
    await session.execute(stmt)
    return len(facts_list)
