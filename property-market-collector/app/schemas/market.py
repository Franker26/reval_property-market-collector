from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_SORTABLE_FIELDS = {
    "price_usd",
    "price_per_m2_total",
    "surface_total",
    "data_quality_score",
    "last_seen_at",
    "days_observed",
}


class MarketSearchRequest(BaseModel):
    # Filtros de clasificación
    status: Optional[str] = None
    operation_type: Optional[str] = None
    property_type: Optional[str] = None

    # Filtros de ubicación
    province: Optional[str] = None
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    location_source: Optional[str] = None

    # Rangos de superficie
    surface_total_min: Optional[float] = None
    surface_total_max: Optional[float] = None
    surface_covered_min: Optional[float] = None
    surface_covered_max: Optional[float] = None

    # Rangos de precio
    price_usd_min: Optional[float] = None
    price_usd_max: Optional[float] = None

    # Rangos de habitaciones (filtran contra listing_entities)
    rooms_min: Optional[int] = None
    rooms_max: Optional[int] = None
    bedrooms_min: Optional[int] = None
    bedrooms_max: Optional[int] = None
    bathrooms_min: Optional[int] = None
    bathrooms_max: Optional[int] = None

    # Calidad y clasificación
    min_data_quality_score: Optional[int] = Field(None, ge=0, le=100)
    market_bucket: Optional[str] = None

    # Flags de datos requeridos (solo aplican si vienen True)
    require_price: Optional[bool] = None
    require_surface: Optional[bool] = None
    require_location: Optional[bool] = None

    # Ordenamiento
    sort_by: Optional[str] = None
    sort_order: str = Field("desc", pattern="^(asc|desc)$")

    # Paginación
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _SORTABLE_FIELDS:
            allowed = ", ".join(sorted(_SORTABLE_FIELDS))
            raise ValueError(f"sort_by debe ser uno de: {allowed}")
        return v


class MarketListingResult(BaseModel):
    listing_id: int
    source: Optional[str] = None
    external_id: Optional[str] = None
    url: Optional[str] = None

    title: Optional[str] = None
    status: Optional[str] = None

    operation_type: Optional[str] = None
    property_type: Optional[str] = None

    price_usd: Optional[float] = None
    surface_total: Optional[float] = None
    surface_covered: Optional[float] = None
    price_per_m2_total: Optional[float] = None
    price_per_m2_covered: Optional[float] = None

    rooms: Optional[int] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    garages: Optional[int] = None

    province: Optional[str] = None
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_source: Optional[str] = None
    geo_cell_id: Optional[str] = None

    thumbnail_url: Optional[str] = None
    image_count: int = 0
    has_images: bool = False

    days_observed: Optional[int] = None
    days_on_market: Optional[int] = None

    price_change_count: Optional[int] = None
    price_delta_pct: Optional[float] = None

    data_quality_score: Optional[int] = None
    market_bucket: Optional[str] = None
    last_seen_at: Optional[datetime] = None


class MarketSearchResponse(BaseModel):
    total: int
    limit: int
    offset: int
    results: list[MarketListingResult]
