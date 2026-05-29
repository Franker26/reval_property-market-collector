"""Hashing estable para comparación de snapshots."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_hash(data: Any) -> str:
    """SHA256 de representación JSON canónica (sorted keys, no espacios)."""
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _extract(payload: dict, *keys: str) -> dict:
    """Extrae las keys presentes en payload, ignorando None."""
    return {k: payload[k] for k in keys if payload.get(k) is not None}


def compute_content_hash(payload: dict) -> str:
    """Hash del contenido normalizado, excluyendo campos volátiles."""
    EXCLUDE = {
        "captured_at", "run_id", "request_id", "discovered_at",
        "random_metadata", "created_at", "updated_at",
    }
    clean = {k: v for k, v in payload.items() if k not in EXCLUDE}
    return _stable_hash(clean)


def compute_price_hash(payload: dict) -> str | None:
    price = payload.get("price") or {}
    data = _extract(
        price,
        "precio", "currency", "expenses", "expenses_currency",
    )
    if not data:
        return None
    return _stable_hash(data)


def compute_availability_hash(payload: dict) -> str | None:
    listing = payload.get("listing") or {}
    data = _extract(listing, "posting_status")
    status = payload.get("status")
    if status:
        data["status"] = status
    if not data:
        return None
    return _stable_hash(data)


def compute_location_hash(payload: dict) -> str | None:
    loc = payload.get("location") or {}
    data = _extract(loc, "direccion", "neighborhood", "city", "province", "lat", "lon")
    if not data:
        return None
    return _stable_hash(data)


def compute_media_hash(payload: dict) -> str | None:
    media = payload.get("media") or {}
    data = _extract(media, "imagen_url", "has_video", "has_tour_360")
    if not data:
        return None
    return _stable_hash(data)
