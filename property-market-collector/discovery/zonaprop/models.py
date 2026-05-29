"""
discovery.zonaprop.models
~~~~~~~~~~~~~~~~~~~~~~~~~
Modelo de datos para un registro de discovery obtenido desde
páginas de listado paginadas.

DiscoveryRecord refleja los campos que parse_posting() extrae del API.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class DiscoveryRecord:
    source: str
    external_id: str
    url: str
    operation_type: Optional[str]
    property_type: Optional[str]
    status: str
    source_modified_at: Optional[datetime]
    price_amount: Optional[int]
    price_currency: Optional[str]
    expenses_amount: Optional[int]
    expenses_currency: Optional[str]
    surface_total: Optional[int]
    surface_covered: Optional[int]
    rooms: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    garages: Optional[int]
    address: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    neighborhood: Optional[str]
    city: Optional[str]
    province_name: Optional[str]
    seller_id: Optional[str]
    seller_name: Optional[str]
    seller_type: Optional[str]
    extra_data: Optional[dict]
    discovery_method: str
    search_url: str
    page: int
    discovered_at: str

    def to_dict(self) -> dict:
        return asdict(self)
