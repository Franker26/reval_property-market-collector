from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Index, Integer, Numeric, Text, TIMESTAMP, UniqueConstraint, text
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import Base


class ZonapropSegment(Base):
    __tablename__ = "zonaprop_segments"
    __table_args__ = (
        UniqueConstraint(
            "portal", "operation_key", "province_key",
            "price_min", "price_max", "surface_min", "surface_max",
            name="uq_zonaprop_segments_boundaries",
        ),
        Index("idx_zonaprop_segments_portal_op_prov", "portal", "operation_key", "province_key"),
        Index("idx_zonaprop_segments_leaf", "is_leaf", "portal"),
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
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("zonaprop_segments.id"))
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_oversized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    # Churn observado (Etapa B): churn diario normalizado medido por url_discovery.
    # churn_ewma NULL + churn_samples_count=0 => tier 'unknown' (exploración).
    # La salida de 'unknown' depende SOLO de churn_samples_count >= min_samples,
    # nunca de churn_ewma IS NOT NULL (puede venir heredado de un split como prior).
    churn_last: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    churn_ewma: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    churn_samples_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_churn_observed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    snapshots: Mapped[list["ZonapropSegmentSnapshot"]] = relationship(back_populates="segment")


class ZonapropSegmentSnapshot(Base):
    __tablename__ = "zonaprop_segment_snapshots"
    __table_args__ = (
        Index("idx_zonaprop_segment_snapshots_captured", "segment_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("zonaprop_segments.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_min: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    surface_max: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    segment: Mapped["ZonapropSegment"] = relationship(back_populates="snapshots")


class ZonapropSegmentScanHistory(Base):
    """
    Registro append-only de cada scan de segmento completado por url_discovery.

    A diferencia de scan_queue (una fila por segmento, sobreescrita en cada ciclo),
    esta tabla conserva la historia: auditoría operativa, calibración del refresh,
    debugging de clasificaciones y dataset futuro de ML. Es además el estado
    durable de los batches de full scan (idempotencia por batch_id + priority).
    """
    __tablename__ = "zonaprop_segment_scan_history"
    __table_args__ = (
        Index("idx_zonaprop_scan_history_seg_at", "segment_id", "scanned_at"),
        Index(
            "idx_zonaprop_scan_history_batch", "batch_id",
            postgresql_where=text("batch_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("zonaprop_segments.id"), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    # Contexto del segmento al momento del scan
    operation_key: Mapped[Optional[str]] = mapped_column(Text)
    province_key: Mapped[Optional[str]] = mapped_column(Text)
    price_min: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    price_max: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_min: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    surface_max: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    total_count: Mapped[Optional[int]] = mapped_column(Integer)
    delta_total_count: Mapped[Optional[int]] = mapped_column(Integer)

    # Decisión que llevó al scan
    tier: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[Optional[str]] = mapped_column(Text)
    age_hours: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    estimated_pages: Mapped[Optional[int]] = mapped_column(Integer)
    batch_id: Mapped[Optional[str]] = mapped_column(Text)

    # Resultado del scan
    new_count: Mapped[Optional[int]] = mapped_column(Integer)
    changed_count: Mapped[Optional[int]] = mapped_column(Integer)
    listings_found: Mapped[Optional[int]] = mapped_column(Integer)
    churn_raw: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    churn_daily: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    churn_ewma: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    churn_samples_count: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
