"""Modelos SQLAlchemy para Reval Market Intelligence."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, ForeignKey, Index, Integer,
    Numeric, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class MarketSegment(Base):
    __tablename__ = "market_segments"
    __table_args__ = (
        Index("idx_market_segments_portal_op_prov", "portal", "operation_key", "province_key"),
        Index("idx_market_segments_leaf", "is_leaf", "portal"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portal: Mapped[str] = mapped_column(Text, nullable=False)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    operation_value: Mapped[int] = mapped_column(Integer, nullable=False)
    province_key: Mapped[str] = mapped_column(Text, nullable=False)
    province_value: Mapped[int] = mapped_column(Integer, nullable=False)
    price_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    total_count: Mapped[Optional[int]] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("market_segments.id"))
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_oversized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    snapshots: Mapped[list["SegmentSnapshot"]] = relationship(back_populates="segment")


class SegmentSnapshot(Base):
    __tablename__ = "segment_snapshots"
    __table_args__ = (
        Index("idx_segment_snapshots_segment_captured", "segment_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_segments.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    segment: Mapped["MarketSegment"] = relationship(back_populates="snapshots")


class MarketSource(Base):
    __tablename__ = "market_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    listings: Mapped[list["ListingEntity"]] = relationship(back_populates="source")
    runs: Mapped[list["CollectionRun"]] = relationship(back_populates="source")
    discovery_events: Mapped[list["DiscoveryEvent"]] = relationship(back_populates="source")


# ── Payload mixin ─────────────────────────────────────────────────────────────
# Ambas tablas (listing_entities y listing_snapshots) tienen los mismos campos
# de payload. listing_entities = estado actual mutable. listing_snapshots = un
# registro por cada cambio de estado (append-only).

class _ListingPayloadMixin:
    """Campos que representan el estado observable de una propiedad."""

    # Disponibilidad
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    source_modified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    # Precio
    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_currency: Mapped[Optional[str]] = mapped_column(Text)
    expenses_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    expenses_currency: Mapped[Optional[str]] = mapped_column(Text)

    # Superficie
    surface_total: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_covered: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    # Habitaciones
    rooms: Mapped[Optional[int]] = mapped_column(Integer)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer)
    bathrooms: Mapped[Optional[int]] = mapped_column(Integer)
    garages: Mapped[Optional[int]] = mapped_column(Integer)

    # Ubicación
    address: Mapped[Optional[str]] = mapped_column(Text)
    lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    lon: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    neighborhood: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    province_name: Mapped[Optional[str]] = mapped_column(Text)

    # Vendedor
    seller_id: Mapped[Optional[str]] = mapped_column(Text)
    seller_name: Mapped[Optional[str]] = mapped_column(Text)
    seller_type: Mapped[Optional[str]] = mapped_column(Text)

    # Campos portal-específicos que no mapean a columnas genéricas
    extra_data: Mapped[Optional[dict]] = mapped_column(JSONB)


class ListingEntity(_ListingPayloadMixin, Base):
    __tablename__ = "listing_entities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("idx_listing_entities_segment_status", "segment_id", "status"),
        Index("idx_listing_entities_status", "status"),
    )

    # Identidad
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text)
    segment_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("market_segments.id"))
    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type: Mapped[Optional[str]] = mapped_column(Text)

    # Hash del payload actual — para detectar si algo cambió en el siguiente scan
    content_hash: Mapped[Optional[str]] = mapped_column(Text)

    # Lifecycle
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_changed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source: Mapped["MarketSource"] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing")
    errors: Mapped[list["CollectionError"]] = relationship(back_populates="listing")


class ListingSnapshot(_ListingPayloadMixin, Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        Index("idx_listing_snapshots_listing_captured", "listing_id", "captured_at"),
        Index("idx_listing_snapshots_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listing_entities.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped["ListingEntity"] = relationship(back_populates="snapshots")


class DiscoveryEvent(Base):
    __tablename__ = "discovery_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_sources.id"), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    search_url: Mapped[Optional[str]] = mapped_column(Text)
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    offset_value: Mapped[Optional[int]] = mapped_column(Integer)
    lastmod: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("collection_runs.id"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    source: Mapped["MarketSource"] = relationship(back_populates="discovery_events")
    run: Mapped[Optional["CollectionRun"]] = relationship(back_populates="discovery_events")


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("market_sources.id"))
    run_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")

    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    duration_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    params_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    stats_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    source: Mapped[Optional["MarketSource"]] = relationship(back_populates="runs")
    errors: Mapped[list["CollectionError"]] = relationship(back_populates="run")
    discovery_events: Mapped[list["DiscoveryEvent"]] = relationship(back_populates="run")


class CollectionError(Base):
    __tablename__ = "collection_errors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("collection_runs.id"))
    source_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("market_sources.id"))
    listing_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("listing_entities.id"))
    external_id: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)

    error_type: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    failed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[Optional["CollectionRun"]] = relationship(back_populates="errors")
    listing: Mapped[Optional["ListingEntity"]] = relationship(back_populates="errors")
