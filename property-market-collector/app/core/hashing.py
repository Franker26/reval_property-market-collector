"""Hashing estable para comparación de estados de listings."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal


# Campos que determinan si el estado de un listing cambió.
# Cambios en estos campos → nuevo snapshot + last_changed_at.
_HASH_KEYS = (
    "price_amount",
    "price_currency",
    "expenses_amount",
    "surface_total",
    "rooms",
    "bedrooms",
    "bathrooms",
    "garages",
    "status",
    # source_modified_at excluido: Zonaprop lo actualiza en refreshes del vendedor,
    # no refleja cambios en los datos estructurados que importan para market intelligence.
    "seller_id",
)


def _normalize(v: object) -> object:
    """Convierte Decimal a int/float para que la serialización JSON sea idéntica
    sin importar si el dato viene de la API (int) o de una entidad SQLAlchemy (Decimal)."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def compute_listing_hash(data: dict) -> str:
    """
    SHA256 de los campos que determinan el estado de un listing.
    Serialización canónica (sorted keys) para consistencia entre fuentes
    (respuesta de API vs entidad ORM).
    """
    subset = {k: _normalize(data.get(k)) for k in _HASH_KEYS}
    serialized = json.dumps(subset, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
