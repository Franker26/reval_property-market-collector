"""Endpoint de búsqueda neutral de publicaciones de mercado."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.db.session import get_db
from app.repositories.market_search import search_facts
from app.schemas.market import MarketListingResult, MarketSearchRequest, MarketSearchResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["market"])


@router.post("/facts/search", response_model=MarketSearchResponse)
async def search_market_facts(
    body: MarketSearchRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
):
    total, rows = await search_facts(db, body)
    results = [_row_to_result(row) for row in rows]
    log.info("market/facts/search: %d resultados (total=%d)", len(results), total)
    return MarketSearchResponse(total=total, limit=body.limit, offset=body.offset, results=results)


def _row_to_result(row) -> MarketListingResult:
    f = row.ListingMarketFacts
    return MarketListingResult(
        listing_id=f.listing_id,
        source=row.source_code,
        external_id=f.external_id,
        url=row.canonical_url,
        title=row.generated_title,
        status=f.status,
        operation_type=f.operation_type,
        property_type=f.property_type,
        price_usd=float(f.price_usd) if f.price_usd is not None else None,
        surface_total=float(f.surface_total) if f.surface_total is not None else None,
        surface_covered=float(f.surface_covered) if f.surface_covered is not None else None,
        price_per_m2_total=float(f.price_per_m2_total) if f.price_per_m2_total is not None else None,
        price_per_m2_covered=float(f.price_per_m2_covered) if f.price_per_m2_covered is not None else None,
        rooms=row.rooms,
        bedrooms=row.bedrooms,
        bathrooms=row.bathrooms,
        garages=row.garages,
        province=f.province,
        city=f.city,
        neighborhood=f.neighborhood,
        latitude=float(f.latitude) if f.latitude is not None else None,
        longitude=float(f.longitude) if f.longitude is not None else None,
        location_source=f.location_source,
        geo_cell_id=f.geo_cell_id,
        thumbnail_url=None,
        image_count=0,
        has_images=False,
        days_observed=f.days_observed,
        days_on_market=f.days_on_market,
        price_change_count=f.price_change_count,
        price_delta_pct=float(f.price_delta_pct) if f.price_delta_pct is not None else None,
        data_quality_score=f.data_quality_score,
        market_bucket=f.market_bucket,
        last_seen_at=f.last_seen_at,
    )
