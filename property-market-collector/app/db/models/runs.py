from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


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
