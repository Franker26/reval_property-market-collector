from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class ListingLocationNormalization(Base):
    """
    Capa de normalización geográfica por publicación.

    Separa el dato de ubicación crudo del portal del dato geográfico
    validado/normalizado. Permite escalar a geocoding externo (Nominatim,
    Photon, PostGIS, etc.) sin modificar listing_entities ni listing_market_facts.

    geo_status values:
      'coordinates' — el portal trajo lat/lon; copiados como normalized.
      'pending'     — sin coordenadas; requiere geocoding futuro.
      'failed'      — intento de geocoding falló; ver geo_error.
      'manual'      — coordenadas corregidas manualmente.
    """
    __tablename__ = "listing_location_normalization"
    __table_args__ = (
        Index("idx_lln_geo_status", "geo_status"),
        Index("idx_lln_norm_province_city", "normalized_province", "normalized_city"),
    )

    id:         Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listing_entities.id"), nullable=False, unique=True
    )

    # ── Datos crudos del portal ───────────────────────────────────────────────
    raw_province:     Mapped[str | None] = mapped_column(Text)
    raw_city:         Mapped[str | None] = mapped_column(Text)
    raw_neighborhood: Mapped[str | None] = mapped_column(Text)
    raw_address:      Mapped[str | None] = mapped_column(Text)
    raw_latitude:     Mapped[float | None] = mapped_column(Numeric(10, 6))
    raw_longitude:    Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Datos normalizados ────────────────────────────────────────────────────
    normalized_country:      Mapped[str | None] = mapped_column(Text)
    normalized_province:     Mapped[str | None] = mapped_column(Text)
    normalized_city:         Mapped[str | None] = mapped_column(Text)
    normalized_neighborhood: Mapped[str | None] = mapped_column(Text)
    normalized_address:      Mapped[str | None] = mapped_column(Text)
    normalized_latitude:     Mapped[float | None] = mapped_column(Numeric(10, 6))
    normalized_longitude:    Mapped[float | None] = mapped_column(Numeric(10, 6))

    # ── Metadata del proveedor de geocoding ───────────────────────────────────
    geo_provider:          Mapped[str | None] = mapped_column(Text)
    geo_provider_place_id: Mapped[str | None] = mapped_column(Text)
    geo_confidence:        Mapped[str | None] = mapped_column(Text)
    geo_status:            Mapped[str | None] = mapped_column(Text)
    geo_error:             Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    listing: Mapped["ListingEntity"] = relationship()  # type: ignore[name-defined]
