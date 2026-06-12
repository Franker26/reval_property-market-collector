"""Repositorio para zonaprop_segments, snapshots, churn y scan history."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.models import (
    ZonapropSegment,
    ZonapropSegmentScanHistory,
    ZonapropSegmentSnapshot,
    ZonapropSegmentScanQueue,
)

import logging

log = logging.getLogger(__name__)


async def upsert_segment(
    session: AsyncSession,
    portal: str,
    operation_key: str,
    operation_value: int,
    province_key: str,
    province_value: int,
    price_min: float,
    price_max: float,
    surface_min: float,
    surface_max: float,
    total_count: Optional[int],
    depth: int,
    parent_id: Optional[int],
    is_leaf: bool,
    is_oversized: bool,
) -> ZonapropSegment:
    """Upsert idempotente por boundaries. Reactiva segmentos existentes; crea nuevos si no existen."""
    stmt = (
        insert(ZonapropSegment)
        .values(
            portal=portal,
            operation_key=operation_key,
            operation_value=operation_value,
            province_key=province_key,
            province_value=province_value,
            price_min=price_min,
            price_max=price_max,
            surface_min=surface_min,
            surface_max=surface_max,
            total_count=total_count,
            depth=depth,
            parent_id=parent_id,
            is_leaf=is_leaf,
            is_oversized=is_oversized,
            status="active",
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_zonaprop_segments_boundaries",
        set_={
            "status": "active",
            "total_count": stmt.excluded.total_count,
            "depth": stmt.excluded.depth,
            "parent_id": stmt.excluded.parent_id,
            "is_leaf": stmt.excluded.is_leaf,
            "is_oversized": stmt.excluded.is_oversized,
            "last_checked_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(ZonapropSegment.id)

    result = await session.execute(stmt)
    segment_id = result.scalar_one()
    segment = await session.get(ZonapropSegment, segment_id)
    return segment  # type: ignore[return-value]


async def sync_pending_scan_queue(session: AsyncSession, portal: str) -> int:
    """Inserta entradas pendientes en la cola de scan para leaf segments activos. Idempotente."""
    subq = (
        select(ZonapropSegment.id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.is_leaf == True,  # noqa: E712
            ZonapropSegment.status == "active",
        )
    )
    stmt = (
        insert(ZonapropSegmentScanQueue)
        .from_select(["segment_id"], subq)
        .on_conflict_do_nothing(index_elements=["segment_id"])
    )
    result = await session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]


async def get_leaf_segments(
    session: AsyncSession,
    portal: str,
    operation_key: Optional[str] = None,
    province_key: Optional[str] = None,
) -> list[ZonapropSegment]:
    stmt = select(ZonapropSegment).where(
        ZonapropSegment.portal == portal,
        ZonapropSegment.is_leaf == True,  # noqa: E712
        ZonapropSegment.status == "active",
    )
    if operation_key:
        stmt = stmt.where(ZonapropSegment.operation_key == operation_key)
    if province_key:
        stmt = stmt.where(ZonapropSegment.province_key == province_key)
    stmt = stmt.order_by(ZonapropSegment.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, segment_id: int) -> Optional[ZonapropSegment]:
    return await session.get(ZonapropSegment, segment_id)


async def save_snapshot(
    session: AsyncSession,
    segment_id: int,
    total_count: int,
    price_min: float,
    price_max: float,
    surface_min: float,
    surface_max: float,
) -> ZonapropSegmentSnapshot:
    snapshot = ZonapropSegmentSnapshot(
        segment_id=segment_id,
        total_count=total_count,
        price_min=price_min,
        price_max=price_max,
        surface_min=surface_min,
        surface_max=surface_max,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def update_total_count(
    session: AsyncSession,
    segment_id: int,
    total_count: int,
) -> None:
    await session.execute(
        update(ZonapropSegment)
        .where(ZonapropSegment.id == segment_id)
        .values(total_count=total_count, last_checked_at=datetime.now(timezone.utc))
    )


async def deactivate_portal_segments(session: AsyncSession, portal: str) -> int:
    """Marca como inactivos todos los segmentos de un portal (pre-rebuild)."""
    result = await session.execute(
        update(ZonapropSegment)
        .where(ZonapropSegment.portal == portal, ZonapropSegment.status == "active")
        .values(status="inactive")
    )
    return result.rowcount  # type: ignore[return-value]


async def invalidate_changed_segments_after_discovery(
    session: AsyncSession,
    portal: str,
    delta_abs_normal: int = 30,
    delta_abs_high: int = 100,
    delta_pct_normal: float = 2.0,
    delta_pct_high: float = 10.0,
) -> dict:
    """
    Compara los dos últimos snapshots de cada segmento activo y marca como pending
    las entradas 'complete' en queue cuyo total_count cambió significativamente.
    Solo evalúa segmentos con al menos 2 snapshots. No toca pending, running ni failed.
    """
    rn = func.row_number().over(
        partition_by=ZonapropSegmentSnapshot.segment_id,
        order_by=ZonapropSegmentSnapshot.captured_at.desc(),
    ).label("rn")

    ranked_subq = (
        select(ZonapropSegmentSnapshot.segment_id, ZonapropSegmentSnapshot.total_count, rn)
        .join(ZonapropSegment, ZonapropSegment.id == ZonapropSegmentSnapshot.segment_id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
            ZonapropSegment.is_leaf == True,  # noqa: E712
        )
        .subquery("ranked")
    )

    latest_subq = (
        select(ranked_subq.c.segment_id, ranked_subq.c.total_count.label("total_count"))
        .where(ranked_subq.c.rn == 1)
        .subquery("latest")
    )

    prev_subq = (
        select(ranked_subq.c.segment_id, ranked_subq.c.total_count.label("total_count"))
        .where(ranked_subq.c.rn == 2)
        .subquery("prev")
    )

    stmt = (
        select(
            latest_subq.c.segment_id,
            latest_subq.c.total_count.label("current_count"),
            prev_subq.c.total_count.label("prev_count"),
            ZonapropSegmentScanQueue.id.label("queue_id"),
        )
        .join(prev_subq, prev_subq.c.segment_id == latest_subq.c.segment_id)
        .join(
            ZonapropSegmentScanQueue,
            ZonapropSegmentScanQueue.segment_id == latest_subq.c.segment_id,
        )
        .where(ZonapropSegmentScanQueue.status == "complete")
    )

    rows = (await session.execute(stmt)).all()

    to_invalidate = []
    for row in rows:
        current_count = row.current_count or 0
        prev_count = row.prev_count or 0
        delta_abs = current_count - prev_count

        if prev_count == 0:
            delta_pct = 100.0 if current_count > 0 else 0.0
        else:
            delta_pct = abs(delta_abs) / prev_count * 100

        if abs(delta_abs) >= delta_abs_normal or delta_pct >= delta_pct_normal:
            priority = (
                "high"
                if abs(delta_abs) >= delta_abs_high or delta_pct >= delta_pct_high
                else "normal"
            )
            to_invalidate.append({
                "queue_id": row.queue_id,
                "segment_id": row.segment_id,
                "current_count": current_count,
                "prev_count": prev_count,
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
                "priority": priority,
            })

    now = datetime.now(timezone.utc)
    for item in to_invalidate:
        reason = (
            f"count_delta: prev={item['prev_count']} current={item['current_count']}"
            f" delta={item['delta_abs']:+d} pct={item['delta_pct']:.1f}%"
        )
        await session.execute(
            update(ZonapropSegmentScanQueue)
            .where(ZonapropSegmentScanQueue.id == item["queue_id"])
            .values(status="pending", reason=reason, priority=item["priority"], updated_at=now)
        )

    top10 = sorted(to_invalidate, key=lambda x: abs(x["delta_abs"]), reverse=True)[:10]

    log.info(
        "invalidate_changed_segments: evaluados=%d invalidados=%d high=%d normal=%d",
        len(rows), len(to_invalidate),
        sum(1 for x in to_invalidate if x["priority"] == "high"),
        sum(1 for x in to_invalidate if x["priority"] == "normal"),
    )
    for item in top10:
        log.info(
            "  seg_id=%-8d delta_abs=%-6+d pct=%.1f%%  priority=%s",
            item["segment_id"], item["delta_abs"], item["delta_pct"], item["priority"],
        )

    return {
        "evaluated": len(rows),
        "invalidated": len(to_invalidate),
        "high": sum(1 for x in to_invalidate if x["priority"] == "high"),
        "normal": sum(1 for x in to_invalidate if x["priority"] == "normal"),
        "top10": top10,
    }


def _compute_volatility(counts: list[int]) -> float:
    """Promedio de |delta ratio| entre snapshots consecutivos (orden cronológico asc)."""
    if len(counts) < 2:
        return 0.0
    ratios: list[float] = []
    for prev, cur in zip(counts, counts[1:]):
        if prev > 0:
            ratios.append(abs(cur - prev) / prev)
        elif cur > 0:
            ratios.append(1.0)
        else:
            ratios.append(0.0)
    return sum(ratios) / len(ratios) if ratios else 0.0


async def select_segments_due_for_refresh(
    session: AsyncSession,
    portal: str,
    cfg,
) -> list[dict]:
    """
    Selecciona hojas activas en estado 'complete' vencidas según su tier, para reencolar.

    Score v2 (Etapa B): churn observado (EWMA diario, señal principal) + volatilidad
    de total_count (count_volatility, secundaria) + volumen (secundaria) → tier
    hot/warm/cold → gap máximo de frescura por negocio. Segmentos sin muestras de
    churn suficientes (churn_samples_count < min_churn_samples) van a tier 'unknown'
    con gap propio: la falta de evidencia nunca los manda a cold.

    El score decide urgencia (orden de reencolado), no si se refresca: todo segmento
    vencido es candidato. Corta por presupuesto de páginas estimadas
    (max_pages_per_cycle) con max_segments_per_cycle como tope secundario; los
    candidatos que no entran en el presupuesto se saltan sin romper el ciclo
    (log skipped_budget_oversized).
    """
    now = datetime.now(timezone.utc)

    # 1) Volatilidad: últimos N snapshots por segmento hoja activo
    rn = func.row_number().over(
        partition_by=ZonapropSegmentSnapshot.segment_id,
        order_by=ZonapropSegmentSnapshot.captured_at.desc(),
    ).label("rn")
    snap_ranked = (
        select(
            ZonapropSegmentSnapshot.segment_id.label("segment_id"),
            ZonapropSegmentSnapshot.total_count.label("total_count"),
            ZonapropSegmentSnapshot.captured_at.label("captured_at"),
            rn,
        )
        .join(ZonapropSegment, ZonapropSegment.id == ZonapropSegmentSnapshot.segment_id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
            ZonapropSegment.is_leaf == True,  # noqa: E712
        )
        .subquery("snap_ranked")
    )
    snap_stmt = (
        select(snap_ranked.c.segment_id, snap_ranked.c.total_count, snap_ranked.c.captured_at)
        .where(snap_ranked.c.rn <= cfg.volatility_lookback_snapshots)
        .order_by(snap_ranked.c.segment_id, snap_ranked.c.captured_at)
    )
    snap_rows = (await session.execute(snap_stmt)).all()
    counts_by_seg: dict[int, list[int]] = {}
    for row in snap_rows:
        counts_by_seg.setdefault(row.segment_id, []).append(int(row.total_count or 0))

    # 2) Hojas activas con entrada de cola en 'complete', con sus señales de churn
    seg_stmt = (
        select(
            ZonapropSegment.id.label("segment_id"),
            ZonapropSegment.total_count.label("total_count"),
            ZonapropSegment.churn_ewma.label("churn_ewma"),
            ZonapropSegment.churn_samples_count.label("churn_samples_count"),
            ZonapropSegmentScanQueue.completed_at.label("completed_at"),
            ZonapropSegmentScanQueue.updated_at.label("updated_at"),
            ZonapropSegmentScanQueue.created_at.label("created_at"),
        )
        .join(ZonapropSegmentScanQueue, ZonapropSegmentScanQueue.segment_id == ZonapropSegment.id)
        .where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
            ZonapropSegment.is_leaf == True,  # noqa: E712
            ZonapropSegment.is_oversized == False,  # noqa: E712
            ZonapropSegment.total_count > 0,
            ZonapropSegmentScanQueue.status == "complete",
        )
    )
    seg_rows = (await session.execute(seg_stmt)).all()

    candidates: list[dict] = []
    for row in seg_rows:
        total_count = int(row.total_count or 0)
        churn_samples = int(row.churn_samples_count or 0)
        count_volatility = _compute_volatility(counts_by_seg.get(row.segment_id, []))

        # La salida de 'unknown' depende SOLO de las muestras, nunca de
        # churn_ewma IS NOT NULL (puede ser un prior heredado de un split).
        has_churn_evidence = churn_samples >= cfg.min_churn_samples

        churn_norm = 0.0
        if has_churn_evidence and row.churn_ewma is not None and cfg.churn_cap > 0:
            churn_norm = min(float(row.churn_ewma) / cfg.churn_cap, 1.0)
        v_norm = min(count_volatility / cfg.volatility_cap, 1.0) if cfg.volatility_cap > 0 else 0.0
        s_norm = min(total_count / cfg.volume_norm_divisor, 1.0) if cfg.volume_norm_divisor > 0 else 0.0
        score = cfg.weight_churn * churn_norm + cfg.weight_volatility * v_norm + cfg.weight_volume * s_norm

        if not has_churn_evidence:
            tier = "unknown"
        elif score >= cfg.hot_score_threshold:
            tier = "hot"
        elif score >= cfg.warm_score_threshold or total_count >= cfg.high_volume_threshold:
            tier = "warm"
        else:
            tier = "cold"
        gap_hours = cfg.gap_hours_for(tier)

        # completed_at NULL (legacy / primer ciclo) → fallback a updated_at/created_at
        ref_ts = row.completed_at or row.updated_at or row.created_at
        if ref_ts is None:
            continue
        if ref_ts.tzinfo is None:
            ref_ts = ref_ts.replace(tzinfo=timezone.utc)
        age_hours = (now - ref_ts).total_seconds() / 3600.0

        if age_hours < cfg.min_age_hours:   # guard anti-loop
            continue
        if age_hours <= gap_hours:          # no vencido
            continue

        postings_per_page = max(int(cfg.postings_per_page), 1)
        candidates.append({
            "segment_id": row.segment_id,
            "tier": tier,
            "score": round(score, 4),
            "age_hours": round(age_hours, 1),
            "gap_hours": gap_hours,
            "churn_ewma": round(float(row.churn_ewma), 4) if row.churn_ewma is not None else None,
            "churn_samples": churn_samples,
            "count_volatility": round(count_volatility, 4),
            "total_count": total_count,
            "estimated_pages": max(math.ceil(total_count / postings_per_page), 1),
            "overdue_ratio": age_hours / gap_hours if gap_hours > 0 else age_hours,
        })

    candidates.sort(key=lambda c: (c["overdue_ratio"], c["score"]), reverse=True)

    # Cupo: presupuesto de páginas estimadas (principal) + cantidad de segmentos
    # (tope secundario). Un candidato que no entra se salta sin romper la selección.
    selected: list[dict] = []
    pages_used = 0
    skipped_budget = 0
    for cand in candidates:
        if len(selected) >= cfg.max_segments_per_cycle:
            break
        if pages_used + cand["estimated_pages"] > cfg.max_pages_per_cycle:
            skipped_budget += 1
            log.info(
                "select_segments_due_for_refresh: skipped_budget_oversized seg_id=%d"
                " estimated_pages=%d pages_used=%d budget=%d",
                cand["segment_id"], cand["estimated_pages"], pages_used, cfg.max_pages_per_cycle,
            )
            continue
        pages_used += cand["estimated_pages"]
        selected.append(cand)

    log.info(
        "select_segments_due_for_refresh: evaluados=%d vencidos=%d seleccionados=%d"
        " (cupo_segmentos=%d cupo_paginas=%d paginas_usadas=%d skipped_budget=%d)"
        " hot=%d warm=%d cold=%d unknown=%d",
        len(seg_rows), len(candidates), len(selected),
        cfg.max_segments_per_cycle, cfg.max_pages_per_cycle, pages_used, skipped_budget,
        sum(1 for c in selected if c["tier"] == "hot"),
        sum(1 for c in selected if c["tier"] == "warm"),
        sum(1 for c in selected if c["tier"] == "cold"),
        sum(1 for c in selected if c["tier"] == "unknown"),
    )
    return selected


# ── Churn observado (Etapa B) ─────────────────────────────────────────────────


def compute_churn_daily(
    new_count: int,
    changed_count: int,
    listings_found: int,
    elapsed_days: float,
    min_elapsed_days: float = 0.5,
) -> Optional[float]:
    """
    Churn diario normalizado de un scan comparable.

    churn_raw = (new + changed) / found, dividido por los días desde el scan
    anterior (piso min_elapsed_days contra rescans inmediatos) y clampeado a 1.0.
    Devuelve None si listings_found == 0 (scan vacío no es evidencia de churn 0).
    """
    if listings_found <= 0:
        return None
    churn_raw = (new_count + changed_count) / listings_found
    churn_daily = churn_raw / max(elapsed_days, min_elapsed_days)
    return min(churn_daily, 1.0)


def blend_churn_ewma(churn_daily: float, ewma_prev: Optional[float], alpha: float) -> float:
    """EWMA del churn diario. Sin valor previo (ni heredado), el primer valor es el churn."""
    if ewma_prev is None:
        return churn_daily
    return alpha * churn_daily + (1.0 - alpha) * ewma_prev


async def update_churn_ewma(
    session: AsyncSession,
    segment_id: int,
    churn_daily: float,
    alpha: float,
) -> dict:
    """
    Registra una observación de churn diario en el segmento: actualiza churn_last,
    mezcla churn_ewma (un prior heredado de split participa como ewma_prev) e
    incrementa churn_samples_count. Devuelve los valores resultantes.
    """
    seg = await session.get(ZonapropSegment, segment_id)
    if seg is None:
        return {}
    ewma_prev = float(seg.churn_ewma) if seg.churn_ewma is not None else None
    new_ewma = blend_churn_ewma(churn_daily, ewma_prev, alpha)
    now = datetime.now(timezone.utc)

    seg.churn_last = churn_daily
    seg.churn_ewma = new_ewma
    seg.churn_samples_count = (seg.churn_samples_count or 0) + 1
    seg.last_churn_observed_at = now
    await session.flush()
    return {
        "churn_ewma": new_ewma,
        "churn_samples_count": seg.churn_samples_count,
    }


_INHERIT_CHURN_SQL = text("""
    WITH donors AS (
        SELECT child.id AS child_id,
               donor.id AS donor_id,
               donor.churn_ewma AS churn_ewma,
               donor.churn_samples_count AS churn_samples_count,
               donor.last_churn_observed_at AS last_churn_observed_at
        FROM zonaprop_segments child
        JOIN LATERAL (
            SELECT p.id, p.churn_ewma, p.churn_samples_count, p.last_churn_observed_at
            FROM zonaprop_segments p
            WHERE p.portal = child.portal
              AND p.operation_key = child.operation_key
              AND p.province_key = child.province_key
              AND p.id <> child.id
              AND p.churn_ewma IS NOT NULL
              AND p.price_min <= child.price_min AND p.price_max >= child.price_max
              AND p.surface_min <= child.surface_min AND p.surface_max >= child.surface_max
            ORDER BY (p.price_max - p.price_min) * (p.surface_max - p.surface_min) ASC, p.id
            LIMIT 1
        ) donor ON true
        WHERE child.portal = :portal
          AND child.is_leaf = true
          AND child.status = 'active'
          AND child.churn_ewma IS NULL
          AND child.churn_samples_count = 0
    )
    UPDATE zonaprop_segments c
    SET churn_ewma = d.churn_ewma,
        last_churn_observed_at = d.last_churn_observed_at,
        churn_samples_count = LEAST(d.churn_samples_count, :samples_cap),
        updated_at = now()
    FROM donors d
    WHERE c.id = d.child_id
    RETURNING c.id AS child_id, d.donor_id AS parent_id
""")


async def inherit_churn_from_parents(
    session: AsyncSession,
    portal: str,
    samples_cap: int,
) -> list[dict]:
    """
    Prior débil para hijos de split: hojas activas sin evidencia propia
    (churn_ewma NULL y 0 muestras) heredan el churn del segmento histórico más
    chico que las contiene (mismo portal/operación/provincia, boundaries
    contenedores). parent_id no sirve acá: los nodos intermedios del árbol nunca
    se persisten, así que el "padre" se resuelve por contención geométrica.

    Idempotente: la condición churn IS NULL garantiza que nunca pisa evidencia
    propia ni una herencia previa. El cap de muestras (= min_samples − 1) asegura
    que el prior no habilita score v2 por sí solo: el hijo sigue 'unknown' hasta
    tener churn propio, y el prior solo inicializa el EWMA en esa primera mezcla.
    """
    result = await session.execute(
        _INHERIT_CHURN_SQL, {"portal": portal, "samples_cap": samples_cap}
    )
    rows = [{"child_id": r.child_id, "parent_id": r.parent_id} for r in result]
    for r in rows:
        log.info(
            "inherit_churn_from_parents: child=%d <- parent=%d (prior débil, cap=%d)",
            r["child_id"], r["parent_id"], samples_cap,
        )
    return rows


# ── Scan history (auditoría + calibración + dataset ML) ───────────────────────


async def get_last_history_total_count(
    session: AsyncSession,
    segment_id: int,
) -> Optional[int]:
    """total_count del último scan registrado en history, para delta_total_count."""
    stmt = (
        select(ZonapropSegmentScanHistory.total_count)
        .where(ZonapropSegmentScanHistory.segment_id == segment_id)
        .order_by(ZonapropSegmentScanHistory.scanned_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def record_scan_history(session: AsyncSession, **fields) -> None:
    """Inserta una fila append-only en zonaprop_segment_scan_history."""
    session.add(ZonapropSegmentScanHistory(**fields))
    await session.flush()


async def count_history_for_batch(
    session: AsyncSession,
    batch_id: str,
    priority: str,
) -> set[int]:
    """
    segment_ids ya procesados para un batch de full scan (batch_id + priority).
    Es el estado durable del batch: scan_queue se sobreescribe por ciclo.
    """
    stmt = (
        select(ZonapropSegmentScanHistory.segment_id)
        .where(
            ZonapropSegmentScanHistory.batch_id == batch_id,
            ZonapropSegmentScanHistory.priority == priority,
        )
        .distinct()
    )
    return {row[0] for row in (await session.execute(stmt)).all()}
