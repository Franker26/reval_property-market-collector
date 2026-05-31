from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import Base


class ZonapropSegmentScanQueue(Base):
    """Cola de trabajo para escanear URLs por segmento de Zonaprop."""
    __tablename__ = "zonaprop_segment_scan_queue"
    __table_args__ = (
        Index("idx_zonaprop_scan_queue_status_seg", "status", "segment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("zonaprop_segments.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    pages_scanned: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    listings_found: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    new_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    changed_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)

    requests_total: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    requests_success: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    requests_failed: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    requests_403: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    requests_429: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    requests_5xx: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    timeouts: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[Optional[float]] = mapped_column(Numeric)
    max_latency_ms: Mapped[Optional[float]] = mapped_column(Numeric)
    cooldown_triggered: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    locked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    segment: Mapped["ZonapropSegment"] = relationship()
