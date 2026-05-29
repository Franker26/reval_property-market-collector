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
    targets: Mapped[list["ListingTarget"]] = relationship(back_populates="source")
    runs: Mapped[list["CollectionRun"]] = relationship(back_populates="source")
    discovery_events: Mapped[list["DiscoveryEvent"]] = relationship(back_populates="source")


class ListingEntity(Base):
    __tablename__ = "listing_entities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text)

    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")

    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_success_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_error_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_changed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source: Mapped["MarketSource"] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing", foreign_keys="[ListingSnapshot.listing_id]")
    errors: Mapped[list["CollectionError"]] = relationship(back_populates="listing")
    change_events: Mapped[list["ListingChangeEvent"]] = relationship(back_populates="listing")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        Index("idx_listing_snapshots_listing_captured", "listing_id", "captured_at"),
        Index("idx_listing_snapshots_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listing_entities.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(JSONB)

    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    price_hash: Mapped[Optional[str]] = mapped_column(Text)
    availability_hash: Mapped[Optional[str]] = mapped_column(Text)
    location_hash: Mapped[Optional[str]] = mapped_column(Text)
    media_hash: Mapped[Optional[str]] = mapped_column(Text)

    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_currency: Mapped[Optional[str]] = mapped_column(Text)
    expenses_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    expenses_currency: Mapped[Optional[str]] = mapped_column(Text)

    surface_total: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_covered: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    rooms: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    bedrooms: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    bathrooms: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    garages: Mapped[Optional[Decimal]] = mapped_column(Numeric)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped["ListingEntity"] = relationship(back_populates="snapshots", foreign_keys=[listing_id])


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


class ListingTarget(Base):
    __tablename__ = "listing_targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("market_sources.id"), nullable=False)
    search_url: Mapped[str] = mapped_column(Text, nullable=False)
    operation_type: Mapped[Optional[str]] = mapped_column(Text)
    property_type: Mapped[Optional[str]] = mapped_column(Text)
    location_text: Mapped[Optional[str]] = mapped_column(Text)

    state: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    last_processed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    next_process_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    last_page_processed: Mapped[Optional[int]] = mapped_column(Integer)
    last_offset_processed: Mapped[Optional[int]] = mapped_column(Integer)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_error_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source: Mapped["MarketSource"] = relationship(back_populates="targets")


class ListingChangeEvent(Base):
    __tablename__ = "listing_change_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("listing_entities.id"), nullable=False)
    previous_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("listing_snapshots.id"))
    new_snapshot_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("listing_snapshots.id"))

    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB)

    detected_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    listing: Mapped["ListingEntity"] = relationship(back_populates="change_events")
    previous_snapshot: Mapped[Optional["ListingSnapshot"]] = relationship(foreign_keys=[previous_snapshot_id])
    new_snapshot: Mapped[Optional["ListingSnapshot"]] = relationship(foreign_keys=[new_snapshot_id])
