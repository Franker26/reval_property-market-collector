"""Repositorio para discovery_events."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiscoveryEvent


async def record(
    session: AsyncSession,
    source_id: int,
    url: str,
    method: str,
    external_id: Optional[str] = None,
    search_url: Optional[str] = None,
    page_number: Optional[int] = None,
    offset_value: Optional[int] = None,
    lastmod: Optional[datetime] = None,
    run_id: Optional[int] = None,
) -> DiscoveryEvent:
    event = DiscoveryEvent(
        source_id=source_id,
        external_id=external_id,
        url=url,
        method=method,
        search_url=search_url,
        page_number=page_number,
        offset_value=offset_value,
        lastmod=lastmod,
        run_id=run_id,
    )
    session.add(event)
    await session.flush()
    return event
