"""
discovery.engine.url_discovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Extrae URLs de publicaciones paginando segmentos hoja.
Portal-agnostic: opera a través de PortalAdapter.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.core.rate_limiter import CooldownError, get_rate_limiter
from discovery.engine.models import PortalAdapter

log = logging.getLogger(__name__)


@dataclass
class _RequestMetrics:
    """Acumula métricas de requests HTTP durante el procesamiento de un segmento."""
    total: int = 0
    success: int = 0
    failed: int = 0
    http_403: int = 0
    http_429: int = 0
    http_5xx: int = 0
    timeouts: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    cooldown_triggered: bool = False

    def avg_latency(self) -> Optional[float]:
        if not self.latencies_ms:
            return None
        return sum(self.latencies_ms) / len(self.latencies_ms)

    def max_latency(self) -> Optional[float]:
        return max(self.latencies_ms) if self.latencies_ms else None

    def to_dict(self) -> dict:
        return {
            "requests_total": self.total,
            "requests_success": self.success,
            "requests_failed": self.failed,
            "requests_403": self.http_403,
            "requests_429": self.http_429,
            "requests_5xx": self.http_5xx,
            "timeouts": self.timeouts,
            "avg_latency_ms": round(self.avg_latency(), 1) if self.avg_latency() is not None else None,
            "max_latency_ms": round(self.max_latency(), 1) if self.max_latency() is not None else None,
            "cooldown_triggered": self.cooldown_triggered,
        }


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
    error_fn: Optional[Callable[..., Awaitable[None]]] = None,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Pagina la API del portal para un segmento y extrae publicaciones.

    Cada posting pasado a *persist_fn* es el dict completo devuelto por
    adapter.parse_posting() más la key "segment_db_id".

    *error_fn* recibe (error_type, http_status, message, retryable) para
    cada fallo HTTP o de conexión, permitiendo persistencia externa.

    Returns: stats con pages_ok, pages_failed, total_found, stopped_early, metrics.
    """
    stats: dict = {
        "pages_ok": 0,
        "pages_failed": 0,
        "total_found": 0,
        "stopped_early": False,
    }
    metrics = _RequestMetrics()
    consecutive_4xx: dict[int, int] = {403: 0, 429: 0}
    seen_ids: set[str] = set()

    page_num = 1
    while True:
        if max_pages is not None and page_num > max_pages:
            stats["stopped_early"] = True
            break

        if cancel_fn is not None and cancel_fn():
            log.info(
                "url_discovery[%s]: parada forzosa — abortando segmento en pág %d",
                adapter.portal, page_num,
            )
            stats["stopped_early"] = True
            break

        try:
            await rate.wait()
        except CooldownError as exc:
            log.error("url_discovery[%s]: cooldown — %s", adapter.portal, exc)
            metrics.cooldown_triggered = True
            stats["stopped_early"] = True
            if error_fn:
                await error_fn("cooldown_abort", None, str(exc), True)
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

        t0 = time.monotonic()
        data = await session.post_json(adapter.api_url, payload)
        elapsed_ms = (time.monotonic() - t0) * 1000
        metrics.total += 1

        if data is None:
            metrics.failed += 1
            metrics.timeouts += 1
            rate.record_error()
            stats["pages_failed"] += 1
            msg = f"página {page_num} sin respuesta (timeout/conexión)"
            log.warning("url_discovery[%s]: %s", adapter.portal, msg)
            if error_fn:
                await error_fn("timeout_or_connection", None, msg, True)
            break

        if "__http_error__" in data:
            status = data["__http_error__"]
            metrics.failed += 1
            metrics.latencies_ms.append(elapsed_ms)
            rate.record_error(http_status=status)
            stats["pages_failed"] += 1

            if status == 403:
                metrics.http_403 += 1
                consecutive_4xx[403] += 1
                consecutive_4xx[429] = 0
                error_type = "http_403"
                retryable = False
            elif status == 429:
                metrics.http_429 += 1
                consecutive_4xx[429] += 1
                consecutive_4xx[403] = 0
                error_type = "http_429"
                retryable = True
            elif status >= 500:
                metrics.http_5xx += 1
                consecutive_4xx = {403: 0, 429: 0}
                error_type = f"http_{status}"
                retryable = True
            else:
                consecutive_4xx = {403: 0, 429: 0}
                error_type = f"http_{status}"
                retryable = status >= 500

            msg = f"HTTP {status} en página {page_num}"
            if error_fn:
                await error_fn(error_type, status, msg, retryable)

            if status in (403, 429):
                log.error("url_discovery[%s]: %s — deteniendo segmento", adapter.portal, msg)
                stats["stopped_early"] = True
            break

        metrics.success += 1
        metrics.latencies_ms.append(elapsed_ms)
        consecutive_4xx = {403: 0, 429: 0}
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

        # Detección de loop infinito: Zonaprop a veces recicla páginas en lugar de devolver vacío
        page_ids = {p["external_id"] for p in parsed if p.get("external_id")}
        if page_ids and page_ids.issubset(seen_ids):
            log.warning(
                "url_discovery[%s]: página %d reciclada (%d IDs ya vistos) — fin del segmento",
                adapter.portal, page_num, len(page_ids),
            )
            stats["recycled_page"] = True
            break
        seen_ids.update(page_ids)

        stats["pages_ok"] += 1
        stats["total_found"] += len(parsed)

        log.debug(
            "url_discovery[%s]: pág %d → %d publicaciones (total: %d)",
            adapter.portal, page_num, len(parsed), stats["total_found"],
        )

        if persist_fn is not None and parsed:
            await persist_fn(parsed, page_num)

        page_num += 1

    stats["metrics"] = metrics.to_dict()
    return stats


async def run_url_discovery(
    adapter: PortalAdapter,
    segments: list,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
    error_fn: Optional[Callable[..., Awaitable[None]]] = None,
    max_pages_per_segment: Optional[int] = None,
    cancel_fn: Optional[Callable[[], bool]] = None,
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
                error_fn=error_fn,
                cancel_fn=cancel_fn,
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
                "metrics": stats.get("metrics", {}),
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
