"""
Lógica de cómputo de listing_market_facts.

Función pura: recibe objetos ORM ya cargados, devuelve un dict listo para
upsert. Sin acceso a DB — testeable de forma aislada.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Garantiza que el datetime tenga tzinfo UTC para comparaciones seguras."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _safe_divide(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None:
        return None
    try:
        if denominator == 0:
            return None
        return numerator / denominator
    except (InvalidOperation, ZeroDivisionError):
        return None


def compute_facts(entity, snapshots: list, location_norm, now: datetime) -> dict:
    """
    Computa todos los market facts para una publicación.

    Args:
        entity:        ListingEntity — estado actual.
        snapshots:     list[ListingSnapshot] — historial completo del listing.
        location_norm: ListingLocationNormalization | None — puede ser None.
        now:           datetime aware (UTC) usado como referencia temporal.

    Returns:
        dict con todos los campos de listing_market_facts, listo para upsert.
    """
    # ── Precio actual en USD ──────────────────────────────────────────────────
    price_usd: Optional[Decimal] = (
        entity.price_amount
        if entity.price_amount is not None and entity.price_currency == "USD"
        else None
    )

    # ── Precio por m² ────────────────────────────────────────────────────────
    price_per_m2_total   = _safe_divide(price_usd, entity.surface_total)
    price_per_m2_covered = _safe_divide(price_usd, entity.surface_covered)

    # ── Historial de precios desde snapshots ──────────────────────────────────
    usd_snaps = sorted(
        [s for s in snapshots if s.price_currency == "USD" and s.price_amount is not None],
        key=lambda s: s.captured_at,
    )

    initial_price_usd:    Optional[Decimal]  = usd_snaps[0].price_amount  if usd_snaps else None
    current_price_usd:    Optional[Decimal]  = price_usd
    min_price_usd:        Optional[Decimal]  = min(s.price_amount for s in usd_snaps) if usd_snaps else None
    max_price_usd:        Optional[Decimal]  = max(s.price_amount for s in usd_snaps) if usd_snaps else None
    price_change_count:   int                = 0
    last_price_change_at: Optional[datetime] = None

    for prev, curr in zip(usd_snaps, usd_snaps[1:]):
        if curr.price_amount != prev.price_amount:
            price_change_count += 1
            last_price_change_at = curr.captured_at

    price_delta_usd: Optional[Decimal] = None
    price_delta_pct: Optional[Decimal] = None
    if initial_price_usd is not None and current_price_usd is not None:
        price_delta_usd = current_price_usd - initial_price_usd
        if initial_price_usd != 0:
            price_delta_pct = (price_delta_usd / initial_price_usd * 100).quantize(Decimal("0.01"))

    # ── Tiempo en mercado ─────────────────────────────────────────────────────
    now_aware = _aware(now)
    publisher_created_at_aware = _aware(entity.publisher_created_at)
    first_seen_at_aware        = _aware(entity.first_seen_at)

    days_published: Optional[int] = (
        (now_aware - publisher_created_at_aware).days
        if publisher_created_at_aware is not None and now_aware is not None
        else None
    )
    days_observed: Optional[int] = (
        (now_aware - first_seen_at_aware).days
        if first_seen_at_aware is not None and now_aware is not None
        else None
    )
    days_on_market = days_published if days_published is not None else days_observed

    # ── Calidad de datos ──────────────────────────────────────────────────────
    has_price   = entity.price_amount is not None and entity.price_currency is not None
    has_surface = entity.surface_total is not None
    has_location = (
        (entity.lat is not None and entity.lon is not None)
        or entity.neighborhood is not None
    )
    has_seller = entity.seller_id is not None or entity.seller_name is not None
    data_quality_score = int(sum([has_price, has_surface, has_location, has_seller]) / 4.0 * 100)

    # ── Ubicación (normalizada si existe, raw como fallback) ──────────────────
    if location_norm and location_norm.normalized_latitude is not None:
        province     = location_norm.normalized_province
        city         = location_norm.normalized_city
        neighborhood = location_norm.normalized_neighborhood
        latitude     = location_norm.normalized_latitude
        longitude    = location_norm.normalized_longitude
        location_src = "normalized"
    else:
        province     = entity.province_name
        city         = entity.city
        neighborhood = entity.neighborhood
        latitude     = entity.lat
        longitude    = entity.lon
        location_src = "raw"

    geo_cell_id: Optional[str] = None
    if latitude is not None and longitude is not None:
        try:
            geo_cell_id = f"{round(float(latitude), 2)}:{round(float(longitude), 2)}"
        except (ValueError, TypeError):
            pass

    # ── Clasificación de mercado ──────────────────────────────────────────────
    parts = [p for p in [entity.operation_type, entity.property_type] if p]
    market_bucket = "_".join(parts) or None

    # ── Metadata de snapshots ─────────────────────────────────────────────────
    last_snapshot_at = max((s.captured_at for s in snapshots), default=None)
    snapshot_count   = len(snapshots)

    return {
        "listing_id":           entity.id,
        "source_id":            entity.source_id,
        "external_id":          entity.external_id,
        "status":               entity.status,
        "operation_type":       entity.operation_type,
        "property_type":        entity.property_type,
        "price_usd":            price_usd,
        "price_currency":       entity.price_currency,
        "surface_total":        entity.surface_total,
        "surface_covered":      entity.surface_covered,
        "price_per_m2_total":   price_per_m2_total,
        "price_per_m2_covered": price_per_m2_covered,
        "publisher_created_at": entity.publisher_created_at,
        "first_seen_at":        entity.first_seen_at,
        "last_seen_at":         entity.last_seen_at,
        "days_published":       days_published,
        "days_observed":        days_observed,
        "days_on_market":       days_on_market,
        "initial_price_usd":    initial_price_usd,
        "current_price_usd":    current_price_usd,
        "min_price_usd":        min_price_usd,
        "max_price_usd":        max_price_usd,
        "price_change_count":   price_change_count,
        "last_price_change_at": last_price_change_at,
        "price_delta_usd":      price_delta_usd,
        "price_delta_pct":      price_delta_pct,
        "has_price":            has_price,
        "has_surface":          has_surface,
        "has_location":         has_location,
        "has_seller":           has_seller,
        "data_quality_score":   data_quality_score,
        "province":             province,
        "city":                 city,
        "neighborhood":         neighborhood,
        "latitude":             latitude,
        "longitude":            longitude,
        "geo_cell_id":          geo_cell_id,
        "location_source":      location_src,
        "market_bucket":        market_bucket,
        "last_snapshot_at":     last_snapshot_at,
        "snapshot_count":       snapshot_count,
    }
