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
import re
from datetime import datetime
from typing import Optional

from app.core.config import get_settings

log = logging.getLogger(__name__)

_SELLER_TYPE = {"1": "particular", "2": "inmobiliaria", "3": "developer"}
_STATUS_MAP = {"ONLINE": "active", "OFFLINE": "offline", "PAUSED": "paused", "RESERVED": "reserved"}

# Publicaciones agrupadores de emprendimientos: no son unidades comprables directamente.
# Aparecen en múltiples segmentos con precios inconsistentes (la API convierte por contexto).
_EXCLUDED_REAL_ESTATE_TYPE_IDS = frozenset({33, 34})  # desarrollos horizontales, verticales


def _feat_int(features: dict, feature_id: str) -> Optional[int]:
    """Extrae el valor entero de una feature por ID (CFT100, CFT1, etc.)."""
    feat = features.get(feature_id)
    if not feat:
        return None
    try:
        return int(float(feat["value"]))
    except (ValueError, KeyError, TypeError):
        return None


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    """Parsea ISO 8601 con offset estilo -0400 (sin separador de colon)."""
    if not s:
        return None
    # Normalizar -0400 → -04:00 para fromisoformat de Python
    normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', s)
    try:
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


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
            "moneda": "2",
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
        """
        Normaliza un posting crudo al schema genérico de listing_entities.
        Devuelve None si el posting no tiene external_id válido.
        """
        external_id = str(raw.get("postingId") or raw.get("id") or "").strip()
        if not external_id:
            return None

        if (raw.get("realEstateType") or {}).get("realEstateTypeId") in _EXCLUDED_REAL_ESTATE_TYPE_IDS:
            return None

        # URL canónica
        raw_url = raw.get("url") or raw.get("link") or ""
        base = self.base_url
        if raw_url.startswith("/"):
            canonical_url = base.rstrip("/") + raw_url
        elif raw_url.startswith("http"):
            canonical_url = raw_url
        else:
            canonical_url = f"{base}/propiedades/{external_id}.html"

        # Tipo de operación y precio — vienen en priceOperationTypes[0]
        price_ops = raw.get("priceOperationTypes") or []
        price_op = price_ops[0] if price_ops else {}
        op_raw = price_op.get("operationType") or {}
        operation_type = (op_raw.get("name") or "").lower() or None

        prices = price_op.get("prices") or []
        first_price = prices[0] if prices else {}
        price_amount = first_price.get("amount")       # int, e.g. 45000
        price_currency = first_price.get("currency")   # "USD" | "ARS"

        # Expensas
        exp = raw.get("expenses")
        if exp and exp.get("amount"):
            expenses_amount = exp["amount"]
            expenses_currency = exp.get("currency")
        else:
            expenses_amount = None
            expenses_currency = None

        # Tipo de propiedad
        type_raw = (
            raw.get("realEstateType")
            or raw.get("propertyType")
            or raw.get("realestateType")
            or {}
        )
        property_type = (type_raw.get("name") or "").lower() or None

        # Features: mainFeatures es un dict {featureId: {value, label, ...}}
        features = raw.get("mainFeatures") or {}
        surface_total   = _feat_int(features, "CFT100")  # Superficie total
        surface_covered = _feat_int(features, "CFT101")  # Superficie cubierta
        surface_unit    = (features.get("CFT100") or {}).get("measure") or None  # e.g. "m²"
        rooms           = _feat_int(features, "CFT1")    # Ambientes
        bedrooms        = _feat_int(features, "CFT2")    # Dormitorios
        bathrooms       = _feat_int(features, "CFT3")    # Baños
        toilettes       = _feat_int(features, "CFT4")    # Toilettes
        garages         = _feat_int(features, "CFT7")    # Cocheras
        antiquity_years = _feat_int(features, "CFT5")    # Antigüedad (años)

        # Ubicación
        loc_data = raw.get("postingLocation") or {}
        address_info = loc_data.get("address") or {}
        address = address_info.get("name")

        geo = (loc_data.get("postingGeolocation") or {}).get("geolocation") or {}
        lat = geo.get("latitude")
        lon = geo.get("longitude")

        # Árbol jerárquico de ubicación: depth 3=ZONA, 2=CIUDAD, 1=PROVINCIA
        loc_levels: dict[int, str] = {}
        node = loc_data.get("location") or {}
        while node:
            depth = node.get("depth")
            name = node.get("name")
            if depth is not None and name:
                loc_levels[depth] = name
            node = node.get("parent") or {}
        neighborhood  = loc_levels.get(3)
        city          = loc_levels.get(2)
        province_name = loc_levels.get(1)

        # Vendedor / publisher
        pub = raw.get("publisher") or {}
        seller_id   = pub.get("publisherId")
        seller_name = pub.get("name")
        seller_type = _SELLER_TYPE.get(str(pub.get("publisherTypeId") or ""))

        # Estado y fecha de modificación
        status = _STATUS_MAP.get(raw.get("status") or "", "unknown")
        source_modified_at = _parse_datetime(raw.get("modified_date"))

        # Nuevos campos con columna propia
        disposition = (features.get("1000019") or {}).get("value") or None
        orientation = (features.get("1000029") or {}).get("value") or None
        generated_title = raw.get("generatedTitle") or None
        description = raw.get("descriptionNormalized") or None

        # extra_data: todo lo demás que no tiene columna propia
        extra: dict = {}

        # Visibilidad de dirección
        addr_vis = address_info.get("visibility")
        if addr_vis:
            extra["address_visibility"] = addr_vis

        # Campos de primer nivel
        for key, dest in (
            ("postingCode",      "posting_code"),
            ("reserved",         "reserved"),
            ("premier",          "premier"),
            ("hasVideos",        "has_videos"),
            ("hasTour",          "has_tour"),
            ("hasPlans",         "has_plans"),
            ("triggerPill",      "trigger_pill"),
            ("alphanumeric_key", "alphanumeric_key"),
            ("whatsApp",         "whatsapp"),
            ("publicationAreaId","publication_area_id"),
        ):
            val = raw.get(key)
            if val is not None and val != "" and val != []:
                extra[dest] = val

        # lowPricePercentage (precio rebajado)
        low_pct = price_op.get("lowPricePercentage")
        if low_pct is not None:
            extra["low_price_percentage"] = low_pct

        # realEstateTypeId
        ret_id = (raw.get("realEstateType") or {}).get("realEstateTypeId")
        if ret_id:
            extra["real_estate_type_id"] = ret_id

        # Publisher extras
        for key, dest in (
            ("url",          "publisher_url"),
            ("mainPhone",    "publisher_phone"),
            ("premier",      "publisher_premier"),
            ("created_date", "publisher_created_date"),
        ):
            val = pub.get(key)
            if val is not None and val != "":
                extra[dest] = val

        # mainFeatures no mapeados a columna
        _KNOWN_FEATURES = {"CFT1", "CFT2", "CFT3", "CFT4", "CFT5", "CFT7",
                           "CFT100", "CFT101", "1000019", "1000029"}
        for feat_id, feat_data in features.items():
            if feat_id not in _KNOWN_FEATURES and feat_data:
                val = feat_data.get("value")
                if val is not None:
                    label = feat_data.get("label", feat_id)
                    extra[f"feature_{feat_id}"] = {"label": label, "value": val}

        return {
            "external_id":        external_id,
            "canonical_url":      canonical_url,
            "operation_type":     operation_type,
            "property_type":      property_type,
            "status":             status,
            "source_modified_at": source_modified_at,
            "price_amount":       price_amount,
            "price_currency":     price_currency,
            "expenses_amount":    expenses_amount,
            "expenses_currency":  expenses_currency,
            "surface_total":      surface_total,
            "surface_covered":    surface_covered,
            "surface_unit":       surface_unit,
            "rooms":              rooms,
            "bedrooms":           bedrooms,
            "bathrooms":          bathrooms,
            "toilettes":          toilettes,
            "garages":            garages,
            "address":            address,
            "lat":                lat,
            "lon":                lon,
            "neighborhood":       neighborhood,
            "city":               city,
            "province_name":      province_name,
            "seller_id":          seller_id,
            "seller_name":        seller_name,
            "seller_type":        seller_type,
            "generated_title":    generated_title,
            "description":        description,
            "antiquity_years":    antiquity_years,
            "disposition":        disposition,
            "orientation":        orientation,
            "extra_data":         extra or None,
        }
