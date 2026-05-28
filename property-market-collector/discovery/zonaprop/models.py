"""
discovery.zonaprop.models
~~~~~~~~~~~~~~~~~~~~~~~~~
Modelo de datos para un registro de discovery obtenido desde
páginas de listado paginadas (no desde sitemaps).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class DiscoveryRecord:
    source: str
    url: str
    external_id: str
    operation_type: str
    discovery_method: str
    search_url: str
    page: int
    discovered_at: str

    def to_dict(self) -> dict:
        return asdict(self)
