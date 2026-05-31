"""Hashing estable para comparación de estados de listings."""
from __future__ import annotations

import hashlib
import json
from typing import Any

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


def compute_listing_hash(data: dict) -> str:
    """
    SHA256 de los campos que determinan el estado de un listing.
    Serialización canónica (sorted keys) para consistencia.
    """
    subset = {k: data.get(k) for k in _HASH_KEYS}
    serialized = json.dumps(subset, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
