"""
discovery.engine.incremental_monitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monitoreo incremental de segmentos. Portal-agnostic.

Consulta el total_count actual de cada segmento hoja,
compara con el snapshot anterior y decide la acción.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import CooldownError, get_rate_limiter
from app.repositories.zonaprop import segments as seg_repo
from discovery.engine.models import PortalAdapter
from discovery.engine.url_discovery import discover_segment

log = logging.getLogger(__name__)


async def _query_count(session, rate, adapter: PortalAdapter, seg) -> Optional[int]:
    try:
        await rate.wait()
    except CooldownError as exc:
        log.error("incremental_monitor[%s]: cooldown — %s", adapter.portal, exc)
        return None

    loc_val = int(getattr(seg, "location_value", None) or getattr(seg, "province_value", 0))
    payload = adapter.build_count_payload(
        page=1,
        operation_value=int(seg.operation_value),
        location_value=loc_val,
        price_min=float(seg.price_min),
        price_max=float(seg.price_max),
        surface_min=float(seg.surface_min),
        surface_max=float(seg.surface_max),
    )
    data = await session.post_json(adapter.api_url, payload)

    if data is None:
        rate.record_error()
        return None
    if "__http_error__" in data:
        rate.record_error(http_status=data["__http_error__"])
        return None

    rate.record_success()
    return adapter.extract_total(data)


def _decide_action(old_count: int, new_count: int, cfg) -> str:
    if old_count == 0:
        return "full_scan" if new_count > 0 else "skip"
    delta_ratio = abs(new_count - old_count) / old_count
    if delta_ratio < cfg.minor_delta_ratio:
        return "skip"
    if delta_ratio < cfg.major_delta_ratio:
        return "partial_scan"
    return "full_scan"


async def run_incremental_monitor(
    adapter: PortalAdapter,
    cfg,
    db_session: AsyncSession,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
    operation_key: Optional[str] = None,
    location_key: Optional[str] = None,
) -> dict:
    """
    Monitorea los segmentos hoja activos del portal.

    *location_key* es el filtro equivalente a province_key en los ORM rows
    (que almacenan la columna como province_key por compatibilidad histórica).
    """
    rate = get_rate_limiter(adapter.rate_limiter_key)
    api_session = await adapter.create_session()

    segments = await seg_repo.get_leaf_segments(
        db_session,
        portal=adapter.portal,
        operation_key=operation_key,
        province_key=location_key,
    )

    agg: dict = {
        "segments_checked": 0,
        "segments_skipped": 0,
        "segments_partial_scan": 0,
        "segments_full_scan": 0,
        "listings_found": 0,
        "snapshots_saved": 0,
    }

    try:
        for seg in segments:
            old_count = seg.total_count or 0
            new_count = await _query_count(api_session, rate, adapter, seg)

            if new_count is None:
                log.warning(
                    "incremental_monitor[%s]: sin respuesta seg_id=%d — omitiendo",
                    adapter.portal, seg.id,
                )
                agg["segments_checked"] += 1
                continue

            action = _decide_action(old_count, new_count, cfg)
            loc_key_val = getattr(seg, "location_key", None) or getattr(seg, "province_key", "?")

            log.info(
                "incremental_monitor[%s]: seg_id=%d op=%s loc=%s old=%d new=%d → %s",
                adapter.portal, seg.id, seg.operation_key, loc_key_val,
                old_count, new_count, action,
            )

            agg["segments_checked"] += 1

            if action == "skip":
                agg["segments_skipped"] += 1
            else:
                max_pages = cfg.partial_scan_pages if action == "partial_scan" else None
                loc_val = int(getattr(seg, "location_value", None) or getattr(seg, "province_value", 0))
                scan_stats = await discover_segment(
                    session=api_session,
                    rate=rate,
                    adapter=adapter,
                    operation_value=int(seg.operation_value),
                    location_value=loc_val,
                    price_min=float(seg.price_min),
                    price_max=float(seg.price_max),
                    surface_min=float(seg.surface_min),
                    surface_max=float(seg.surface_max),
                    segment_db_id=seg.id,
                    max_pages=max_pages,
                    persist_fn=persist_fn,
                )
                agg["listings_found"] += scan_stats["total_found"]
                if action == "partial_scan":
                    agg["segments_partial_scan"] += 1
                else:
                    agg["segments_full_scan"] += 1

            await seg_repo.save_snapshot(
                db_session,
                segment_id=seg.id,
                total_count=new_count,
                price_min=float(seg.price_min),
                price_max=float(seg.price_max),
                surface_min=float(seg.surface_min),
                surface_max=float(seg.surface_max),
            )
            await seg_repo.update_total_count(db_session, seg.id, new_count)
            await db_session.flush()
            agg["snapshots_saved"] += 1

    finally:
        await api_session.close()

    return agg
