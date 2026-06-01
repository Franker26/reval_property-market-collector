from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.listings import ListingEntity
from app.db.models.market_facts import ListingMarketFacts
from app.db.models.portals import MarketSource
from app.schemas.market import MarketSearchRequest

_SORTABLE_COLS = {
    "price_usd": ListingMarketFacts.price_usd,
    "price_per_m2_total": ListingMarketFacts.price_per_m2_total,
    "surface_total": ListingMarketFacts.surface_total,
    "data_quality_score": ListingMarketFacts.data_quality_score,
    "last_seen_at": ListingMarketFacts.last_seen_at,
    "days_observed": ListingMarketFacts.days_observed,
}


def _build_conditions(req: MarketSearchRequest) -> list:
    c = []

    # Clasificación
    if req.status is not None:
        c.append(ListingMarketFacts.status == req.status)
    if req.operation_type is not None:
        c.append(ListingMarketFacts.operation_type == req.operation_type)
    if req.property_type is not None:
        c.append(ListingMarketFacts.property_type == req.property_type)

    # Ubicación
    if req.province is not None:
        c.append(ListingMarketFacts.province == req.province)
    if req.city is not None:
        c.append(ListingMarketFacts.city == req.city)
    if req.neighborhood is not None:
        c.append(ListingMarketFacts.neighborhood == req.neighborhood)
    if req.location_source is not None:
        c.append(ListingMarketFacts.location_source == req.location_source)

    # Superficie
    if req.surface_total_min is not None:
        c.append(ListingMarketFacts.surface_total >= req.surface_total_min)
    if req.surface_total_max is not None:
        c.append(ListingMarketFacts.surface_total <= req.surface_total_max)
    if req.surface_covered_min is not None:
        c.append(ListingMarketFacts.surface_covered >= req.surface_covered_min)
    if req.surface_covered_max is not None:
        c.append(ListingMarketFacts.surface_covered <= req.surface_covered_max)

    # Precio
    if req.price_usd_min is not None:
        c.append(ListingMarketFacts.price_usd >= req.price_usd_min)
    if req.price_usd_max is not None:
        c.append(ListingMarketFacts.price_usd <= req.price_usd_max)

    # Habitaciones (filtran contra listing_entities, ya joined)
    if req.rooms_min is not None:
        c.append(ListingEntity.rooms >= req.rooms_min)
    if req.rooms_max is not None:
        c.append(ListingEntity.rooms <= req.rooms_max)
    if req.bedrooms_min is not None:
        c.append(ListingEntity.bedrooms >= req.bedrooms_min)
    if req.bedrooms_max is not None:
        c.append(ListingEntity.bedrooms <= req.bedrooms_max)
    if req.bathrooms_min is not None:
        c.append(ListingEntity.bathrooms >= req.bathrooms_min)
    if req.bathrooms_max is not None:
        c.append(ListingEntity.bathrooms <= req.bathrooms_max)

    # Calidad y clasificación
    if req.min_data_quality_score is not None:
        c.append(ListingMarketFacts.data_quality_score >= req.min_data_quality_score)
    if req.market_bucket is not None:
        c.append(ListingMarketFacts.market_bucket == req.market_bucket)

    # Flags de datos requeridos
    if req.require_price:
        c.append(ListingMarketFacts.price_usd.is_not(None))
    if req.require_surface:
        c.append(ListingMarketFacts.surface_total.is_not(None))
    if req.require_location:
        c.append(ListingMarketFacts.latitude.is_not(None))
        c.append(ListingMarketFacts.longitude.is_not(None))

    return c


def _build_order(req: MarketSearchRequest):
    if req.sort_by:
        col = _SORTABLE_COLS[req.sort_by]
        return col.asc() if req.sort_order == "asc" else col.desc()
    return None


async def search_facts(
    db: AsyncSession,
    req: MarketSearchRequest,
) -> tuple[int, list]:
    conditions = _build_conditions(req)
    where_clause = and_(*conditions) if conditions else True

    # Query principal
    stmt = (
        select(
            ListingMarketFacts,
            ListingEntity.canonical_url,
            ListingEntity.generated_title,
            ListingEntity.rooms,
            ListingEntity.bedrooms,
            ListingEntity.bathrooms,
            ListingEntity.garages,
            MarketSource.code.label("source_code"),
        )
        .join(ListingEntity, ListingMarketFacts.listing_id == ListingEntity.id)
        .outerjoin(MarketSource, ListingMarketFacts.source_id == MarketSource.id)
        .where(where_clause)
    )

    custom_order = _build_order(req)
    if custom_order is not None:
        stmt = stmt.order_by(custom_order)
    else:
        stmt = stmt.order_by(
            ListingMarketFacts.data_quality_score.desc(),
            ListingMarketFacts.last_seen_at.desc(),
        )

    stmt = stmt.limit(req.limit).offset(req.offset)

    # Query de conteo (sin paginación)
    count_stmt = (
        select(func.count())
        .select_from(ListingMarketFacts)
        .join(ListingEntity, ListingMarketFacts.listing_id == ListingEntity.id)
        .where(where_clause)
    )

    rows_result = await db.execute(stmt)
    count_result = await db.execute(count_stmt)

    rows = rows_result.all()
    total = count_result.scalar_one()

    return total, rows
