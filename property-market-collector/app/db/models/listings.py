from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


class ListingEntity(Base):
    __tablename__ = "listing_entities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        Index("idx_listing_entities_status", "status"),
    )

    # ── Identidad ─────────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")

    # ── Publicación ───────────────────────────────────────────────────────────
    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type: Mapped[Optional[str]] = mapped_column(Text)
    generated_title: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # ── Precio ────────────────────────────────────────────────────────────────
    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_currency: Mapped[Optional[str]] = mapped_column(Text)
    expenses_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    expenses_currency: Mapped[Optional[str]] = mapped_column(Text)

    # ── Superficie y características ──────────────────────────────────────────
    surface_total: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_covered: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_unit: Mapped[Optional[str]] = mapped_column(Text)
    rooms: Mapped[Optional[int]] = mapped_column(Integer)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer)
    bathrooms: Mapped[Optional[int]] = mapped_column(Integer)
    toilettes: Mapped[Optional[int]] = mapped_column(Integer)
    garages: Mapped[Optional[int]] = mapped_column(Integer)
    antiquity_years: Mapped[Optional[int]] = mapped_column(Integer)
    disposition: Mapped[Optional[str]] = mapped_column(Text)
    orientation: Mapped[Optional[str]] = mapped_column(Text)

    # ── Ubicación ─────────────────────────────────────────────────────────────
    address: Mapped[Optional[str]] = mapped_column(Text)
    neighborhood: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    province_name: Mapped[Optional[str]] = mapped_column(Text)
    lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    lon: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # ── Vendedor ──────────────────────────────────────────────────────────────
    seller_id: Mapped[Optional[str]] = mapped_column(Text)
    seller_name: Mapped[Optional[str]] = mapped_column(Text)
    seller_type: Mapped[Optional[str]] = mapped_column(Text)

    # ── Extra (datos portal-específicos) ──────────────────────────────────────
    extra_data: Mapped[Optional[dict]] = mapped_column(JSONB)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    source_modified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_changed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    content_hash: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source: Mapped["MarketSource"] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing")
    errors: Mapped[list["CollectionError"]] = relationship(back_populates="listing")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        Index("idx_listing_snapshots_listing_captured", "listing_id", "captured_at"),
        Index("idx_listing_snapshots_content_hash", "content_hash"),
    )

    # ── Identidad del snapshot ────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listing_entities.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Estado en el momento del snapshot ─────────────────────────────────────
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    source_modified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    # ── Publicación ───────────────────────────────────────────────────────────
    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type: Mapped[Optional[str]] = mapped_column(Text)
    generated_title: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # ── Precio ────────────────────────────────────────────────────────────────
    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_currency: Mapped[Optional[str]] = mapped_column(Text)
    expenses_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    expenses_currency: Mapped[Optional[str]] = mapped_column(Text)

    # ── Superficie y características ──────────────────────────────────────────
    surface_total: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_covered: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_unit: Mapped[Optional[str]] = mapped_column(Text)
    rooms: Mapped[Optional[int]] = mapped_column(Integer)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer)
    bathrooms: Mapped[Optional[int]] = mapped_column(Integer)
    toilettes: Mapped[Optional[int]] = mapped_column(Integer)
    garages: Mapped[Optional[int]] = mapped_column(Integer)
    antiquity_years: Mapped[Optional[int]] = mapped_column(Integer)
    disposition: Mapped[Optional[str]] = mapped_column(Text)
    orientation: Mapped[Optional[str]] = mapped_column(Text)

    # ── Ubicación ─────────────────────────────────────────────────────────────
    address: Mapped[Optional[str]] = mapped_column(Text)
    neighborhood: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    province_name: Mapped[Optional[str]] = mapped_column(Text)
    lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    lon: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # ── Vendedor ──────────────────────────────────────────────────────────────
    seller_id: Mapped[Optional[str]] = mapped_column(Text)
    seller_name: Mapped[Optional[str]] = mapped_column(Text)
    seller_type: Mapped[Optional[str]] = mapped_column(Text)

    # ── Extra (datos portal-específicos) ──────────────────────────────────────
    extra_data: Mapped[Optional[dict]] = mapped_column(JSONB)

    # ── Record creation ───────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped["ListingEntity"] = relationship(back_populates="snapshots")

