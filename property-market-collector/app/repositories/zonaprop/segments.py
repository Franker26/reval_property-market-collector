"""Repositorio para zonaprop_segments y zonaprop_segment_snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.models import ZonapropSegment, ZonapropSegmentSnapshot, ZonapropSegmentScanQueue

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

    Hoja activa = is_leaf + status active + total_count > 0 + no oversized.
    El score combina volatilidad histórica (snapshots) y volumen (total_count) →
    tier (hot/warm/cold) → gap objetivo. Vencido si age_hours > gap, con guard de edad
    mínima anti-loop. Ordena por más vencido (overdue ratio) y luego mayor score; aplica cupo.
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

    # 2) Hojas activas con entrada de cola en 'complete' — incluye priority para detectar primer ciclo
    seg_stmt = (
        select(
            ZonapropSegment.id.label("segment_id"),
            ZonapropSegment.total_count.label("total_count"),
            ZonapropSegmentScanQueue.completed_at.label("completed_at"),
            ZonapropSegmentScanQueue.updated_at.label("updated_at"),
            ZonapropSegmentScanQueue.created_at.label("created_at"),
            ZonapropSegmentScanQueue.priority.label("queue_priority"),
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
        volatility = _compute_volatility(counts_by_seg.get(row.segment_id, []))
        v_norm = min(volatility / cfg.volatility_cap, 1.0) if cfg.volatility_cap > 0 else 0.0
        s_norm = min(total_count / cfg.volume_norm_divisor, 1.0) if cfg.volume_norm_divisor > 0 else 0.0
        score = cfg.weight_volatility * v_norm + cfg.weight_volume * s_norm

        if score >= cfg.hot_score_threshold:
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

        # Segmentos que nunca pasaron por un ciclo de refresh (priority no es 'refresh_*'):
        # se incluyen si superan min_age_hours, independientemente del gap del tier.
        # El overdue_ratio sigue usando gap_hours del tier para que el sort sea justo.
        is_first_refresh = not (row.queue_priority or "").startswith("refresh_")
        effective_gap = cfg.min_age_hours if is_first_refresh else gap_hours

        if age_hours <= effective_gap:      # no vencido
            continue

        candidates.append({
            "segment_id": row.segment_id,
            "tier": tier,
            "score": round(score, 4),
            "age_hours": round(age_hours, 1),
            "gap_hours": gap_hours,
            "volatility": round(volatility, 4),
            "total_count": total_count,
            "is_first_refresh": is_first_refresh,
            "overdue_ratio": age_hours / gap_hours if gap_hours > 0 else age_hours,
        })

    candidates.sort(key=lambda c: (c["overdue_ratio"], c["score"]), reverse=True)
    selected = candidates[: cfg.max_segments_per_cycle]

    first_refresh_count = sum(1 for c in selected if c["is_first_refresh"])
    log.info(
        "select_segments_due_for_refresh: evaluados=%d vencidos=%d seleccionados=%d (cupo=%d)"
        " hot=%d warm=%d cold=%d primer_refresh=%d",
        len(seg_rows), len(candidates), len(selected), cfg.max_segments_per_cycle,
        sum(1 for c in selected if c["tier"] == "hot"),
        sum(1 for c in selected if c["tier"] == "warm"),
        sum(1 for c in selected if c["tier"] == "cold"),
        first_refresh_count,
    )
    return selected
