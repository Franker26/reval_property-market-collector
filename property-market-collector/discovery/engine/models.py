"""
discovery.engine.models
~~~~~~~~~~~~~~~~~~~~~~~
Modelos compartidos por el engine de discovery y los adapters de portal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class SegmentNode:
    """Nodo del árbol adaptativo de segmentos (representación en memoria)."""
    portal: str
    operation_key: str
    operation_value: int
    location_key: str
    location_value: int
    price_min: float
    price_max: float
    surface_min: float
    surface_max: float
    depth: int = 0
    parent_db_id: Optional[int] = None
    total_count: Optional[int] = None
    is_leaf: bool = False
    is_oversized: bool = False
    db_id: Optional[int] = None


@runtime_checkable
class PortalAdapter(Protocol):
    """
    Protocolo que cada portal debe implementar para usar el engine genérico.

    El engine nunca importa módulos específicos de un portal — solo llama
    a estos métodos. Un portal nuevo solo necesita crear una clase que
    implemente este protocolo y no requiere modificar el engine.
    """

    @property
    def portal(self) -> str:
        """Nombre canónico del portal, ej: 'zonaprop'."""
        ...

    @property
    def api_url(self) -> str:
        """URL del endpoint de búsqueda/listado (POST o GET según portal)."""
        ...

    @property
    def base_url(self) -> str:
        """URL base del portal para construir URLs canónicas."""
        ...

    @property
    def rate_limiter_key(self) -> str:
        """Clave del rate limiter a usar (ej: 'zonaprop_api')."""
        ...

    async def create_session(self):
        """Crea y retorna una sesión HTTP lista para usar."""
        ...

    def build_count_payload(
        self,
        page: int,
        operation_value: int,
        location_value: Optional[int],
        price_min: float,
        price_max: float,
        surface_min: float,
        surface_max: float,
    ) -> dict:
        """Construye el payload de la petición de conteo/listado para el portal."""
        ...

    def extract_total(self, data: dict) -> Optional[int]:
        """Extrae el total_count de la respuesta del portal."""
        ...

    def extract_postings(self, data: dict) -> list[dict]:
        """Extrae la lista de publicaciones crudas de la respuesta."""
        ...

    def parse_posting(self, raw: dict) -> Optional[dict]:
        """
        Parsea una publicación cruda a dict normalizado con:
        external_id, canonical_url, operation_type, property_type.
        """
        ...
