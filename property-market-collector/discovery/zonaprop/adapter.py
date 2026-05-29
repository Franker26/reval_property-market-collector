"""
discovery.zonaprop.adapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Implementación de PortalAdapter para Zonaprop.

Concentra todo lo específico del portal:
- Construcción del payload para POST /rplis-api/postings
- Parseo de respuesta (total_count, listado de publicaciones)
- Creación de sesión HTTP con TLS fingerprint de Chrome (curl_cffi)

El engine genérico llama estos métodos sin saber nada de Zonaprop.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import get_settings

log = logging.getLogger(__name__)


class ZonapropAdapter:
    """
    Adapter de Zonaprop para el discovery engine.

    Un portal nuevo solo necesita crear una clase similar e implementar
    los mismos métodos para ser compatible con el engine.
    """

    # ── Propiedades del portal ────────────────────────────────────────────────

    @property
    def portal(self) -> str:
        return "zonaprop"

    @property
    def api_url(self) -> str:
        return get_settings().zonaprop_api_postings_url

    @property
    def base_url(self) -> str:
        return get_settings().zonaprop_base_url

    @property
    def rate_limiter_key(self) -> str:
        return "zonaprop_api"

    # ── Sesión HTTP ───────────────────────────────────────────────────────────

    async def create_session(self):
        from sources.session_manager import create_zonaprop_session
        settings = get_settings()
        return await create_zonaprop_session(
            warmup_url=settings.zonaprop_warmup_url,
            browser=None,
            user_agent=settings.zonaprop_user_agent,
            base_url=settings.zonaprop_base_url,
        )

    # ── Payload ───────────────────────────────────────────────────────────────

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
        """
        Payload para POST /rplis-api/postings filtrado por segmento.
        superficieCubierta=2 → superficie total; idunidaddemedida=1 → m².
        province → filtro de provincia/zona.
        """
        return {
            "q": None,
            "direccion": None,
            "moneda": "",
            "preciomin": int(price_min) if price_min and price_min > 0 else None,
            "preciomax": int(price_max) if price_max is not None else None,
            "services": "",
            "general": "",
            "searchbykeyword": "",
            "amenidades": "",
            "caracteristicasprop": None,
            "comodidades": "",
            "disposicion": None,
            "roomType": "",
            "outside": "",
            "areaPrivativa": "",
            "areaComun": "",
            "multipleRets": "",
            "tipoDePropiedad": "",
            "subtipoDePropiedad": None,
            "tipoDeOperacion": str(operation_value),
            "garages": None,
            "antiguedad": None,
            "expensasminimo": None,
            "expensasmaximo": None,
            "withoutguarantor": None,
            "habitacionesminimo": 0,
            "habitacionesmaximo": 0,
            "ambientesminimo": 0,
            "ambientesmaximo": 0,
            "banos": None,
            "superficieCubierta": 2,
            "idunidaddemedida": 1,
            "metroscuadradomin": int(surface_min) if surface_min and surface_min > 0 else None,
            "metroscuadradomax": int(surface_max) if surface_max is not None else None,
            "tipoAnunciante": "ALL",
            "grupoTipoDeMultimedia": "",
            "publicacion": None,
            "sort": "relevance",
            "etapaDeDesarrollo": "",
            "auctions": None,
            "polygonApplied": None,
            "idInmobiliaria": None,
            "excludePostingContacted": "",
            "banks": "",
            "places": "",
            "condominio": "",
            "preTipoDeOperacion": "",
            "pagina": page,
            "city": None,
            "province": location_value,
            "zone": None,
            "valueZone": None,
            "subZone": None,
            "coordenates": None,
        }

    # ── Parseo de respuesta ───────────────────────────────────────────────────

    def extract_total(self, data: dict) -> Optional[int]:
        """Extrae total_count de la respuesta de Zonaprop."""
        paging = data.get("paging")
        if isinstance(paging, dict):
            val = paging.get("total")
            if isinstance(val, int):
                return val
        raw = data.get("totalPosting") or data.get("totalPostings")
        if raw is not None:
            try:
                return int(str(raw).replace(".", "").replace(",", ""))
            except ValueError:
                pass
        for key in ("totalCount", "total", "count"):
            val = data.get(key)
            if isinstance(val, int):
                return val
        return None

    def extract_postings(self, data: dict) -> list[dict]:
        """Extrae la lista de publicaciones crudas de la respuesta."""
        for key in ("listPostings", "listObjects", "postings", "items", "results", "data"):
            items = data.get(key)
            if isinstance(items, list) and items:
                return items
        return []

    def parse_posting(self, raw: dict) -> Optional[dict]:
        """Normaliza un posting crudo a {external_id, canonical_url, operation_type, property_type}."""
        external_id = str(raw.get("postingId") or raw.get("id") or "").strip()
        if not external_id:
            return None

        raw_url = raw.get("url") or raw.get("link") or ""
        base = self.base_url
        if raw_url.startswith("/"):
            canonical_url = base.rstrip("/") + raw_url
        elif raw_url.startswith("http"):
            canonical_url = raw_url
        else:
            canonical_url = f"{base}/propiedades/{external_id}.html"

        op_raw = raw.get("operationType") or raw.get("operation") or {}
        operation_type = (op_raw.get("name") or "").lower() if isinstance(op_raw, dict) else None

        type_raw = raw.get("propertyType") or raw.get("realestateType") or raw.get("type") or {}
        property_type = (type_raw.get("name") or "").lower() if isinstance(type_raw, dict) else None

        return {
            "external_id": external_id,
            "canonical_url": canonical_url,
            "operation_type": operation_type or None,
            "property_type": property_type or None,
        }
