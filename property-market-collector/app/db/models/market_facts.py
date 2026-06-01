from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class ListingMarketFacts(Base):
    """
    Capa analítica derivada de listing_entities + listing_snapshots.

    Un registro por publicación. Pre-computa métricas de mercado para que
    Reval ACM pueda consultar datos sin acoplarse al pipeline de extracción.

    Actualización: el job jobs/build_market_facts.py recalcula esta tabla
    en modo incremental (listings modificados) o full (todo).

    No contiene lógica de scoring de comparables ni de tasación.
    """
    __tablename__ = "listing_market_facts"
    __table_args__ = (
        Index("idx_lmf_operation_property", "operation_type", "property_type"),
        Index("idx_lmf_province_city", "province", "city"),
        Index("idx_lmf_price_usd", "price_usd"),
        Index("idx_lmf_price_per_m2", "price_per_m2_total"),
        Index("idx_lmf_status", "status"),
        Index("idx_lmf_market_bucket", "market_bucket"),
        Index("idx_lmf_days_on_market", "days_on_market"),
    )

    # ── Identidad ─────────────────────────────────────────────────────────────
    id:         Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("listing_entities.id"), nullable=False, unique=True
    )

    # ── Denormalizados para queries rápidas ───────────────────────────────────
    source_id:      Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("market_sources.id"))
    external_id:    Mapped[Optional[str]] = mapped_column(Text)
    status:         Mapped[Optional[str]] = mapped_column(Text)
    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type:  Mapped[Optional[str]] = mapped_column(Text)

    # ── Precio actual normalizado ─────────────────────────────────────────────
    price_usd:            Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_currency:       Mapped[Optional[str]]     = mapped_column(Text)
    surface_total:        Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_covered:      Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_per_m2_total:   Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_per_m2_covered: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    # ── Tiempo en mercado ─────────────────────────────────────────────────────
    publisher_created_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    first_seen_at:        Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_seen_at:         Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    days_published:       Mapped[Optional[int]]      = mapped_column(Integer)
    days_observed:        Mapped[Optional[int]]      = mapped_column(Integer)
    days_on_market:       Mapped[Optional[int]]      = mapped_column(Integer)

    # ── Historial de precios ──────────────────────────────────────────────────
    initial_price_usd:    Mapped[Optional[Decimal]]  = mapped_column(Numeric)
    current_price_usd:    Mapped[Optional[Decimal]]  = mapped_column(Numeric)
    min_price_usd:        Mapped[Optional[Decimal]]  = mapped_column(Numeric)
    max_price_usd:        Mapped[Optional[Decimal]]  = mapped_column(Numeric)
    price_change_count:   Mapped[Optional[int]]      = mapped_column(Integer)
    last_price_change_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    price_delta_usd:      Mapped[Optional[Decimal]]  = mapped_column(Numeric)
    price_delta_pct:      Mapped[Optional[Decimal]]  = mapped_column(Numeric)

    # ── Calidad de datos ──────────────────────────────────────────────────────
    has_price:          Mapped[Optional[bool]] = mapped_column(Boolean)
    has_surface:        Mapped[Optional[bool]] = mapped_column(Boolean)
    has_location:       Mapped[Optional[bool]] = mapped_column(Boolean)
    has_seller:         Mapped[Optional[bool]] = mapped_column(Boolean)
    data_quality_score: Mapped[Optional[int]]  = mapped_column(Integer)

    # ── Ubicación (normalizada si existe, raw como fallback) ──────────────────
    province:        Mapped[Optional[str]]     = mapped_column(Text)
    city:            Mapped[Optional[str]]     = mapped_column(Text)
    neighborhood:    Mapped[Optional[str]]     = mapped_column(Text)
    latitude:        Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    longitude:       Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    geo_cell_id:     Mapped[Optional[str]]     = mapped_column(Text)
    location_source: Mapped[Optional[str]]     = mapped_column(Text)

    # ── Clasificación de mercado ──────────────────────────────────────────────
    market_bucket: Mapped[Optional[str]] = mapped_column(Text)

    # ── Metadata del build ────────────────────────────────────────────────────
    last_snapshot_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    snapshot_count:   Mapped[Optional[int]]      = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    listing: Mapped["ListingEntity"] = relationship()  # type: ignore[name-defined]
