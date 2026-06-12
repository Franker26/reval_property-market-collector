"""Repositorio para zonaprop_segment_scan_queue — cola de escaneo por segmento."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ZonapropSegment, ZonapropSegmentScanQueue

log = logging.getLogger(__name__)

_STALE_AFTER_HOURS = 6
_MAX_ATTEMPTS = 3

# Orden de consumo. Semántica: priority = orden operativo; reason = trazabilidad.
#   high              → invalidación estructural post-discovery (máxima prioridad)
#   refresh_hot       → segmentos ya identificados como críticos; ni el full scan los posterga
#   full_scan_compare → 2do ciclo de salida en vivo (genera el primer churn del parque)
#   full_scan_baseline→ 1er ciclo de salida en vivo (construye base comparable)
#   normal/NULL       → segmentos estructuralmente nuevos (primer scan, máxima info nueva)
#   refresh_unknown   → exploración: sin evidencia de churn, nunca enterrado bajo warm/cold
#   refresh_warm/cold → refresh dentro del gap máximo de frescura por negocio
_PRIORITY_RANK = case(
    (ZonapropSegmentScanQueue.priority == "high", 1),
    (ZonapropSegmentScanQueue.priority == "refresh_hot", 2),
    (ZonapropSegmentScanQueue.priority == "full_scan_compare", 3),
    (ZonapropSegmentScanQueue.priority == "full_scan_baseline", 4),
    (ZonapropSegmentScanQueue.priority == "normal", 5),
    (ZonapropSegmentScanQueue.priority.is_(None), 5),
    (ZonapropSegmentScanQueue.priority == "refresh_unknown", 6),
    (ZonapropSegmentScanQueue.priority == "refresh_warm", 7),
    (ZonapropSegmentScanQueue.priority == "refresh_cold", 8),
    else_=9,
)


async def reset_stale_running(session: AsyncSession) -> int:
    """Devuelve a pending los runs que llevan más de 6h en estado running."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_STALE_AFTER_HOURS)
    result = await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(
            ZonapropSegmentScanQueue.status == "running",
            ZonapropSegmentScanQueue.locked_at < cutoff,
        )
        .values(status="pending", locked_at=None, updated_at=datetime.now(timezone.utc))
    )
    return result.rowcount  # type: ignore[return-value]


async def reset_all_running(session: AsyncSession) -> int:
    """Resetea TODOS los segmentos en 'running' a 'pending'. Llamar solo al arrancar."""
    result = await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.status == "running")
        .values(status="pending", locked_at=None, updated_at=datetime.now(timezone.utc))
    )
    return result.rowcount  # type: ignore[return-value]


async def get_pending(session: AsyncSession, portal: str) -> list[ZonapropSegmentScanQueue]:
    """Retorna entradas pendientes para segmentos activos del portal, con segment cargado."""
    stmt = (
        select(ZonapropSegmentScanQueue)
        .options(selectinload(ZonapropSegmentScanQueue.segment))
        .join(ZonapropSegment, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
        .where(
            ZonapropSegmentScanQueue.status == "pending",
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
        )
        .order_by(
            _PRIORITY_RANK,
            ZonapropSegmentScanQueue.completed_at.asc().nulls_first(),
            ZonapropSegmentScanQueue.id,
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_by_status(session: AsyncSession, portal: Optional[str] = None) -> dict[str, int]:
    """Devuelve conteo de entradas agrupadas por status. Útil para health/dashboard."""
    stmt = (
        select(ZonapropSegmentScanQueue.status, func.count().label("n"))
        .join(ZonapropSegment, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
    )
    if portal:
        stmt = stmt.where(ZonapropSegment.portal == portal)
    stmt = stmt.group_by(ZonapropSegmentScanQueue.status)
    result = await session.execute(stmt)
    return {row.status: row.n for row in result}


async def mark_started(session: AsyncSession, run_id: int) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(status="running", locked_at=now, started_at=now, updated_at=now)
    )


async def mark_complete(
    session: AsyncSession,
    run_id: int,
    pages_scanned: int = 0,
    listings_found: int = 0,
    new_count: int = 0,
    changed_count: int = 0,
    requests_total: int = 0,
    requests_success: int = 0,
    requests_failed: int = 0,
    requests_403: int = 0,
    requests_429: int = 0,
    requests_5xx: int = 0,
    timeouts: int = 0,
    avg_latency_ms: Optional[float] = None,
    max_latency_ms: Optional[float] = None,
    cooldown_triggered: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(
            status="complete",
            completed_at=now,
            pages_scanned=pages_scanned,
            listings_found=listings_found,
            new_count=new_count,
            changed_count=changed_count,
            requests_total=requests_total,
            requests_success=requests_success,
            requests_failed=requests_failed,
            requests_403=requests_403,
            requests_429=requests_429,
            requests_5xx=requests_5xx,
            timeouts=timeouts,
            avg_latency_ms=avg_latency_ms,
            max_latency_ms=max_latency_ms,
            cooldown_triggered=cooldown_triggered,
            updated_at=now,
        )
    )


async def mark_pending(
    session: AsyncSession,
    run_id: int,
    last_error: Optional[str] = None,
) -> None:
    values: dict = {"status": "pending", "locked_at": None, "updated_at": datetime.now(timezone.utc)}
    if last_error is not None:
        values["last_error"] = last_error
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(**values)
    )


def _build_refresh_reason(item: dict) -> str:
    """Reason trazable: permite reconstruir la decisión del refresh monitor."""
    tier = item["tier"]
    if tier == "unknown":
        return (
            f"refresh:unknown;age_hours={item['age_hours']};gap_hours={item['gap_hours']};"
            f"churn_samples={item['churn_samples']};reason=no_churn_history;"
            f"estimated_pages={item['estimated_pages']}"
        )
    return (
        f"refresh:{tier};age_hours={item['age_hours']};gap_hours={item['gap_hours']};"
        f"score={item['score']};churn_ewma={item['churn_ewma']};"
        f"churn_samples={item['churn_samples']};count_volatility={item['count_volatility']};"
        f"volume={item['total_count']};estimated_pages={item['estimated_pages']}"
    )


async def enqueue_refresh(session: AsyncSession, items: list[dict]) -> dict:
    """
    Reencola por staleness los segmentos seleccionados por el refresh monitor.

    Idempotente: solo afecta entradas hoy en 'complete' (la cola es una fila por segment_id),
    por lo que un segundo run no duplica trabajo ni toca pending/running/failed.
    Setea priority='refresh_<tier>' y reason con las señales del score v2.
    """
    now = datetime.now(timezone.utc)
    enqueued = 0
    by_tier = {"hot": 0, "warm": 0, "cold": 0, "unknown": 0}
    for item in items:
        tier = item["tier"]
        result = await session.execute(
            update(ZonapropSegmentScanQueue)
            .where(
                ZonapropSegmentScanQueue.segment_id == item["segment_id"],
                ZonapropSegmentScanQueue.status == "complete",
            )
            .values(
                status="pending",
                priority=f"refresh_{tier}",
                reason=_build_refresh_reason(item),
                locked_at=None,
                attempt_count=0,
                updated_at=now,
            )
        )
        affected = result.rowcount or 0
        if affected:
            enqueued += affected
            by_tier[tier] += affected
    return {"enqueued": enqueued, "by_tier": by_tier}


async def enqueue_full_scan(
    session: AsyncSession,
    portal: str,
    scan_mode: str,
    batch_id: str,
    max_pages_per_cycle: int,
    postings_per_page: int,
) -> dict:
    """
    Reencola segmentos para un ciclo de full scan (salida en vivo).

    scan_mode ∈ {'baseline', 'compare'} → priority 'full_scan_<mode>'.
    Universo: hojas activas refreshables (no oversized, total_count > 0).
    Idempotencia por batch: se excluyen los segmentos ya procesados para este
    batch_id+priority (estado durable en scan_history) y los hoy pending/running
    (en vuelo); solo se tocan entradas 'complete'. Respeta presupuesto de páginas
    estimadas por ejecución — correr repetidamente hasta agotar el universo.
    """
    import math

    from app.repositories.zonaprop import segments as seg_repo

    priority = f"full_scan_{scan_mode}"
    reason = f"full_scan:{scan_mode};batch_id={batch_id};reason=initial_churn_{scan_mode}"

    done_ids = await seg_repo.count_history_for_batch(session, batch_id, priority)

    stmt = (
        select(
            ZonapropSegment.id.label("segment_id"),
            ZonapropSegment.total_count.label("total_count"),
            ZonapropSegmentScanQueue.status.label("queue_status"),
        )
        .join(ZonapropSegmentScanQueue, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
            ZonapropSegment.is_leaf == True,  # noqa: E712
            ZonapropSegment.is_oversized == False,  # noqa: E712
            ZonapropSegment.total_count > 0,
        )
        .order_by(ZonapropSegment.id)
    )
    rows = (await session.execute(stmt)).all()

    universe = len(rows)
    already_done = 0
    in_flight = 0
    not_enqueueable = 0
    enqueued = 0
    pages_used = 0
    skipped_budget = 0
    now = datetime.now(timezone.utc)
    per_page = max(postings_per_page, 1)

    for row in rows:
        if row.segment_id in done_ids:
            already_done += 1
            continue
        if row.queue_status in ("pending", "running"):
            in_flight += 1
            continue
        if row.queue_status != "complete":
            not_enqueueable += 1   # failed: no lo pisa el full scan
            continue

        estimated_pages = max(math.ceil(int(row.total_count or 0) / per_page), 1)
        if pages_used + estimated_pages > max_pages_per_cycle:
            skipped_budget += 1
            continue

        result = await session.execute(
            update(ZonapropSegmentScanQueue)
            .where(
                ZonapropSegmentScanQueue.segment_id == row.segment_id,
                ZonapropSegmentScanQueue.status == "complete",
            )
            .values(
                status="pending",
                priority=priority,
                reason=f"{reason};estimated_pages={estimated_pages}",
                locked_at=None,
                attempt_count=0,
                updated_at=now,
            )
        )
        if result.rowcount:
            enqueued += 1
            pages_used += estimated_pages

    stats = {
        "universe": universe,
        "already_done": already_done,
        "in_flight": in_flight,
        "not_enqueueable": not_enqueueable,
        "enqueued": enqueued,
        "estimated_pages": pages_used,
        "skipped_budget": skipped_budget,
        "remaining": universe - already_done - in_flight - enqueued,
    }
    log.info("enqueue_full_scan[%s/%s]: %s", scan_mode, batch_id, stats)
    return stats


async def count_by_priority(session: AsyncSession, portal: Optional[str] = None) -> dict[str, int]:
    """Distribución de entradas pending por priority. Útil para dashboard del refresh."""
    stmt = (
        select(ZonapropSegmentScanQueue.priority, func.count().label("n"))
        .join(ZonapropSegment, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
        .where(ZonapropSegmentScanQueue.status == "pending")
    )
    if portal:
        stmt = stmt.where(ZonapropSegment.portal == portal)
    stmt = stmt.group_by(ZonapropSegmentScanQueue.priority)
    result = await session.execute(stmt)
    return {(row.priority or "unset"): row.n for row in result}


async def mark_failed(session: AsyncSession, run_id: int, error: str) -> None:
    result = await session.execute(
        select(ZonapropSegmentScanQueue.attempt_count)
        .where(ZonapropSegmentScanQueue.id == run_id)
    )
    attempt_count = (result.scalar_one_or_none() or 0) + 1
    new_status = "failed" if attempt_count >= _MAX_ATTEMPTS else "pending"
    await session.execute(
        update(ZonapropSegmentScanQueue)
        .where(ZonapropSegmentScanQueue.id == run_id)
        .values(
            status=new_status,
            attempt_count=attempt_count,
            last_error=error,
            locked_at=None,
            updated_at=datetime.now(timezone.utc),
        )
    )
