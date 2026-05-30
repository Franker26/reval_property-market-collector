from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, Text, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base


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
