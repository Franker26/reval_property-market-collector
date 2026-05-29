"""
discovery.zonaprop.incremental_monitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Thin wrapper de Zonaprop sobre el engine genérico de monitoreo incremental.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from discovery.engine.incremental_monitor import run_incremental_monitor as _engine_run
from discovery.zonaprop.adapter import ZonapropAdapter
from discovery.zonaprop.segment_config import SegmentConfig


async def run_incremental_monitor(
    cfg: SegmentConfig,
    db_session: AsyncSession,
    source_id: int,
    portal: str = "zonaprop",
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
    operation_key: Optional[str] = None,
    province_key: Optional[str] = None,
) -> dict:
    """Monitorea segmentos hoja de Zonaprop usando el engine genérico."""
    adapter = ZonapropAdapter()
    return await _engine_run(
        adapter=adapter,
        cfg=cfg,
        db_session=db_session,
        persist_fn=persist_fn,
        operation_key=operation_key,
        location_key=province_key,
    )
