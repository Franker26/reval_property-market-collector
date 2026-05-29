"""
discovery.engine.url_discovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Extrae URLs de publicaciones paginando segmentos hoja.
Portal-agnostic: opera a través de PortalAdapter.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from app.core.rate_limiter import CooldownError, get_rate_limiter
from discovery.engine.models import PortalAdapter

log = logging.getLogger(__name__)


async def discover_segment(
    session,
    rate,
    adapter: PortalAdapter,
    operation_value: int,
    location_value: Optional[int],
    price_min: float,
    price_max: float,
    surface_min: float,
    surface_max: float,
    segment_db_id: Optional[int] = None,
    max_pages: Optional[int] = None,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
) -> dict:
    """
    Pagina la API del portal para un segmento y extrae publicaciones.

    Cada posting pasado a *persist_fn* es el dict completo devuelto por
    adapter.parse_posting() más la key "segment_db_id".

    Returns: stats con pages_ok, pages_failed, total_found, stopped_early.
    """
    stats: dict = {
        "pages_ok": 0,
        "pages_failed": 0,
        "total_found": 0,
        "stopped_early": False,
    }

    page_num = 1
    while True:
        if max_pages is not None and page_num > max_pages:
            stats["stopped_early"] = True
            break

        try:
            await rate.wait()
        except CooldownError as exc:
            log.error("url_discovery[%s]: cooldown — %s", adapter.portal, exc)
            stats["stopped_early"] = True
            break

        payload = adapter.build_count_payload(
            page=page_num,
            operation_value=operation_value,
            location_value=location_value,
            price_min=price_min,
            price_max=price_max,
            surface_min=surface_min,
            surface_max=surface_max,
        )
        data = await session.post_json(adapter.api_url, payload)

        if data is None:
            rate.record_error()
            stats["pages_failed"] += 1
            log.warning("url_discovery[%s]: página %d sin respuesta", adapter.portal, page_num)
            break

        if "__http_error__" in data:
            status = data["__http_error__"]
            rate.record_error(http_status=status)
            stats["pages_failed"] += 1
            if status in (403, 429):
                log.error("url_discovery[%s]: HTTP %d — deteniendo segmento", adapter.portal, status)
                stats["stopped_early"] = True
            break

        rate.record_success()

        raw_postings = adapter.extract_postings(data)
        if not raw_postings:
            log.debug("url_discovery[%s]: página %d vacía — fin del segmento", adapter.portal, page_num)
            break

        parsed = [
            {**p, "segment_db_id": segment_db_id}
            for raw in raw_postings
            if (p := adapter.parse_posting(raw))
        ]

        stats["pages_ok"] += 1
        stats["total_found"] += len(parsed)

        log.debug(
            "url_discovery[%s]: pág %d → %d publicaciones (total: %d)",
            adapter.portal, page_num, len(parsed), stats["total_found"],
        )

        if persist_fn is not None and parsed:
            await persist_fn(parsed, page_num)

        page_num += 1

    return stats


async def run_url_discovery(
    adapter: PortalAdapter,
    segments: list,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
    max_pages_per_segment: Optional[int] = None,
) -> dict:
    """
    Ejecuta url_discovery sobre una lista de segmentos hoja.

    *segments* puede ser lista de SegmentNode o de MarketSegment (ORM).
    Ambos exponen los atributos necesarios (operation_value, location_value, etc.).

    Returns: agg stats + per_segment list con stopped_early por segmento.
    """
    rate = get_rate_limiter(adapter.rate_limiter_key)
    session = await adapter.create_session()

    agg: dict = {
        "segments_processed": 0,
        "segments_failed": 0,
        "total_found": 0,
        "per_segment": [],
    }

    try:
        for seg in segments:
            seg_db_id = getattr(seg, "db_id", None) or getattr(seg, "id", None)
            op_val = int(getattr(seg, "operation_value"))
            loc_val = int(getattr(seg, "location_value", None) or getattr(seg, "province_value", 0))
            p_min = float(getattr(seg, "price_min"))
            p_max = float(getattr(seg, "price_max"))
            s_min = float(getattr(seg, "surface_min"))
            s_max = float(getattr(seg, "surface_max"))
            op_key = getattr(seg, "operation_key", "?")
            loc_key = getattr(seg, "location_key", None) or getattr(seg, "province_key", "?")

            log.info(
                "url_discovery[%s]: op=%s loc=%s s=[%g-%g] p=[%g-%g]",
                adapter.portal, op_key, loc_key, s_min, s_max, p_min, p_max,
            )

            stats = await discover_segment(
                session=session,
                rate=rate,
                adapter=adapter,
                operation_value=op_val,
                location_value=loc_val,
                price_min=p_min,
                price_max=p_max,
                surface_min=s_min,
                surface_max=s_max,
                segment_db_id=seg_db_id,
                max_pages=max_pages_per_segment,
                persist_fn=persist_fn,
            )

            agg["segments_processed"] += 1
            agg["total_found"] += stats["total_found"]
            if stats["pages_failed"] > 0 and stats["total_found"] == 0:
                agg["segments_failed"] += 1

            agg["per_segment"].append({
                "segment_id": seg_db_id,
                "op_key": op_key,
                "loc_key": loc_key,
                "stopped_early": stats["stopped_early"],
                "total_found": stats["total_found"],
                "pages_ok": stats["pages_ok"],
                "pages_failed": stats["pages_failed"],
            })

            log.info(
                "url_discovery[%s]: op=%s loc=%s → %d encontradas (ok=%d fail=%d%s)",
                adapter.portal, op_key, loc_key, stats["total_found"],
                stats["pages_ok"], stats["pages_failed"],
                " STOPPED_EARLY" if stats["stopped_early"] else "",
            )
    finally:
        await session.close()

    return agg
