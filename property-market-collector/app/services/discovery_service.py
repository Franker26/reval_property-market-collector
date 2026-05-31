"""
Servicio de discovery: orquesta las 3 fases del pipeline de segmentos.
Cada función crea un collection_run para trazabilidad.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.db.session import get_async_session_factory
from app.repositories import collection_errors as errors_repo
from app.repositories import collection_runs as runs_repo
from app.repositories import listings as listings_repo
from app.repositories import sources as sources_repo
from app.repositories.zonaprop import segments as seg_repo
from app.repositories.zonaprop import scan_queue as run_repo

log = logging.getLogger(__name__)

_ALERT_ERROR_RATE_THRESHOLD = int(os.getenv("ALERT_ERROR_RATE_THRESHOLD", "10"))
_ALERT_CONSECUTIVE_4XX_THRESHOLD = int(os.getenv("ALERT_CONSECUTIVE_4XX_THRESHOLD", "3"))

# ── Cancellation ──────────────────────────────────────────────────────────────

_cancel_flags: dict[str, bool] = {}


class _RunCancelled(Exception):
    pass


def request_cancel(run_type: str) -> None:
    _cancel_flags[run_type] = True


def is_cancel_requested(run_type: str) -> bool:
    return _cancel_flags.get(run_type, False)


def _reset_cancel(run_type: str) -> None:
    _cancel_flags.pop(run_type, None)


# ── Fase 1: Segment Discovery (semanal) ───────────────────────────────────────


async def run_segment_discovery(
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    operations: Optional[list[str]] = None,
    locations: Optional[list[str]] = None,
    mode: str = "manual",
) -> dict:
    """
    Construye el árbol adaptativo de segmentos precio × superficie.
    Desactiva los segmentos previos y persiste los nuevos con su snapshot inicial.
    """
    from discovery.zonaprop.segment_config import load_config
    from discovery.zonaprop.segment_discovery import run_segment_discovery as _discover

    _reset_cancel("segment_discovery")

    cfg = load_config()
    if operations:
        cfg.operations = {k: v for k, v in cfg.operations.items() if k in operations}
    if locations:
        cfg.locations = {k: v for k, v in cfg.locations.items() if k in locations}

    factory = get_async_session_factory()

    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            return {"error": f"source '{source_code}' not found"}
        source_id = source.id
        run = await runs_repo.start(
            session,
            run_type="segment_discovery",
            source_id=source_id,
            params={"portal": portal, "operations": operations, "locations": locations},
            mode=mode,
        )
        await session.commit()
        run_id = run.id

    from app.core.alerts import dispatch
    await dispatch(
        "run_started", "warning",
        f"segment_discovery iniciado para {portal}",
        {"run_id": run_id, "portal": portal, "operations": str(operations or "all"), "locations": str(locations or "all")},
    )

    async with factory() as session:
        async with session.begin():
            await seg_repo.deactivate_portal_segments(session, portal)

    leaf_count = 0
    oversized_count = 0

    async def on_leaf(node) -> None:
        nonlocal leaf_count, oversized_count
        if is_cancel_requested("segment_discovery"):
            raise _RunCancelled("Cancelación solicitada por el usuario")
        leaf_count += 1
        if node.is_oversized:
            oversized_count += 1
        async with factory() as session:
            async with session.begin():
                db_seg = await seg_repo.upsert_segment(
                    session=session,
                    portal=node.portal,
                    operation_key=node.operation_key,
                    operation_value=node.operation_value,
                    province_key=node.location_key,
                    province_value=node.location_value,
                    price_min=node.price_min,
                    price_max=node.price_max,
                    surface_min=node.surface_min,
                    surface_max=node.surface_max,
                    total_count=node.total_count,
                    depth=node.depth,
                    parent_id=node.parent_db_id,
                    is_leaf=True,
                    is_oversized=node.is_oversized,
                )
                node.db_id = db_seg.id
                await seg_repo.save_snapshot(
                    session=session,
                    segment_id=db_seg.id,
                    total_count=node.total_count or 0,
                    price_min=node.price_min,
                    price_max=node.price_max,
                    surface_min=node.surface_min,
                    surface_max=node.surface_max,
                )

    try:
        await _discover(cfg, on_leaf_found=on_leaf)
        final_status = "success"
    except _RunCancelled:
        log.info("discovery_service: segment_discovery cancelado por el usuario (run_id=%d)", run_id)
        final_status = "cancelled"
    except Exception as exc:
        log.error("discovery_service: segment_discovery falló — %s", exc)
        final_status = "failed"
        from app.core.alerts import dispatch
        await dispatch(
            "run_failed", "critical",
            f"segment_discovery falló: {exc}",
            {"run_id": run_id, "portal": portal},
        )

    stats = {"leaf_count": leaf_count, "oversized_count": oversized_count}
    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    async with factory() as session:
        async with session.begin():
            new_runs = await seg_repo.sync_pending_scan_queue(session, portal)

    from app.core.alerts import dispatch
    await dispatch(
        "run_completed" if final_status == "success" else "run_failed",
        "warning" if final_status == "success" else "critical",
        f"segment_discovery {final_status} para {portal}",
        {"run_id": run_id, "portal": portal, "leaves": leaf_count, "oversized": oversized_count, "new_runs": new_runs},
    )

    log.info(
        "discovery_service: segment_discovery run_id=%d status=%s leaves=%d oversized=%d new_runs=%d",
        run_id, final_status, leaf_count, oversized_count, new_runs,
    )
    return {"run_id": run_id, "status": final_status, **stats, "new_runs": new_runs}


# ── Fase 2a: URL Discovery con ventana horaria y resumabilidad ────────────────


async def run_url_discovery_window(
    stop_at: datetime,
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    mode: str = "scheduled",
) -> dict:
    """
    Consume runs pendientes de zonaprop_segment_scan_queue hasta alcanzar stop_at.
    Cada segmento se procesa completo; el chequeo de tiempo ocurre entre segmentos.
    """
    _reset_cancel("url_discovery")

    from discovery.zonaprop.url_discovery import run_url_discovery as _discover

    factory = get_async_session_factory()

    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            return {"error": f"source '{source_code}' not found"}
        source_id = source.id

    async with factory() as session:
        async with session.begin():
            stale = await run_repo.reset_stale_running(session)
    if stale > 0:
        log.info("url_discovery_window: %d runs colgados → pending", stale)

    async with factory() as session:
        pending = await run_repo.get_pending(session, portal)

    if not pending:
        log.info("url_discovery_window: no hay segmentos pendientes")
        return {"status": "idle", "processed": 0, "complete": 0}

    log.info(
        "url_discovery_window: %d segmentos pendientes — stop_at=%s",
        len(pending), stop_at.strftime("%H:%M %Z"),
    )

    # Crear collection_run para trazabilidad
    async with factory() as session:
        col_run = await runs_repo.start(
            session,
            run_type="url_discovery_window",
            source_id=source_id,
            params={"portal": portal, "pending_segments": len(pending),
                    "stop_at": stop_at.isoformat(), "mode": mode},
            mode=mode,
        )
        await session.commit()
        col_run_id = col_run.id

    from app.core.alerts import dispatch
    await dispatch(
        "run_started", "warning",
        f"url_discovery_window iniciado para {portal}",
        {"run_id": col_run_id, "portal": portal, "pending_segments": len(pending),
         "stop_at": stop_at.strftime("%H:%M %Z"), "mode": mode},
    )

    stats: dict = {"processed": 0, "complete": 0, "stopped_early": 0, "failed": 0}
    window_error_count = 0

    for run in pending:
        if is_cancel_requested("url_discovery"):
            log.info("url_discovery_window: cancelación solicitada — deteniendo")
            break
        if datetime.now(timezone.utc) >= stop_at.astimezone(timezone.utc):
            log.info("url_discovery_window: ventana horaria alcanzada — deteniendo")
            break

        async with factory() as session:
            async with session.begin():
                await run_repo.mark_started(session, run.id)

        total_found = 0
        changed_count = 0
        segment_id = run.segment_id

        async def persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_found, changed_count
            from app.repositories import snapshots as snap_repo
            async with factory() as sess:
                async with sess.begin():
                    results = await listings_repo.upsert_batch(
                        session=sess,
                        source_id=source_id,
                        postings=postings,
                    )
                    for entity, changed in results:
                        if changed:
                            posting = next(p for p in postings if p["external_id"] == entity.external_id)
                            await snap_repo.create_from_posting(
                                session=sess,
                                listing_id=entity.id,
                                posting=posting,
                                content_hash=entity.content_hash,
                            )
                            changed_count += 1
                    total_found += len(postings)

        async def make_error_fn(run_id: int, seg_id: int):
            async def error_fn(
                error_type: str,
                http_status: Optional[int],
                message: str,
                retryable: bool,
            ) -> None:
                nonlocal window_error_count
                window_error_count += 1
                async with factory() as sess:
                    async with sess.begin():
                        await errors_repo.record(
                            session=sess,
                            error_type=error_type,
                            run_id=run_id,
                            source_id=source_id,
                            http_status=http_status,
                            error_message=message,
                            retryable=retryable,
                        )
                if window_error_count >= _ALERT_ERROR_RATE_THRESHOLD:
                    from app.core.alerts import dispatch
                    await dispatch(
                        "error_rate_exceeded", "warning",
                        f"url_discovery: {window_error_count} errores en la ventana actual",
                        {"portal": portal, "threshold": _ALERT_ERROR_RATE_THRESHOLD},
                    )
            return error_fn

        error_fn = await make_error_fn(run.id, segment_id)

        try:
            seg_stats = await _discover([run.segment], persist_fn=persist, error_fn=error_fn)
            per_seg = seg_stats.get("per_segment", [{}])
            seg_info = per_seg[0] if per_seg else {}
            stopped_early = seg_info.get("stopped_early", False)
            pages_ok = seg_info.get("pages_ok", 0)
            seg_metrics = seg_info.get("metrics", {})

            if stopped_early:
                async with factory() as session:
                    async with session.begin():
                        await run_repo.mark_pending(session, run.id)
                stats["stopped_early"] += 1
                log.info(
                    "url_discovery_window: segmento %d detenido antes de completar — devuelto a pending",
                    run.segment_id,
                )
                break
            else:
                async with factory() as session:
                    async with session.begin():
                        await run_repo.mark_complete(
                            session, run.id,
                            pages_scanned=pages_ok,
                            listings_found=total_found,
                            new_count=total_found - changed_count,
                            changed_count=changed_count,
                            **seg_metrics,
                        )
                stats["complete"] += 1
                log.info(
                    "url_discovery_window: segmento %d completo — %d publicaciones (%d páginas)",
                    run.segment_id, total_found, pages_ok,
                )
        except Exception as exc:
            log.error("url_discovery_window: error en segmento %d — %s", run.segment_id, exc)
            async with factory() as session:
                async with session.begin():
                    await run_repo.mark_failed(session, run.id, str(exc))
            stats["failed"] += 1

        stats["processed"] += 1

    final_status = "cancelled" if is_cancel_requested("url_discovery") else "success"
    async with factory() as session:
        await runs_repo.finish(session, col_run_id, status=final_status, stats=stats)
        await session.commit()

    from app.core.alerts import dispatch
    await dispatch(
        "run_completed", "warning",
        f"url_discovery_window {final_status} para {portal}",
        {"run_id": col_run_id, "portal": portal, "mode": mode, **stats},
    )

    return {"run_id": col_run_id, "status": final_status, **stats}


# ── Fase 3: Incremental Monitor ───────────────────────────────────────────────


async def run_incremental_monitor(
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    operation_key: Optional[str] = None,
    location_key: Optional[str] = None,
) -> dict:
    """
    Compara total_count actual con el snapshot anterior y rescanea si cambió.
    """
    _reset_cancel("incremental_monitor")

    from discovery.zonaprop.segment_config import load_config
    from discovery.zonaprop.incremental_monitor import run_incremental_monitor as _monitor

    cfg = load_config()
    factory = get_async_session_factory()

    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            return {"error": f"source '{source_code}' not found"}
        source_id = source.id
        run = await runs_repo.start(
            session,
            run_type="incremental_monitor",
            source_id=source_id,
            params={"portal": portal},
        )
        await session.commit()
        run_id = run.id

    total_written = 0

    async def persist(postings: list[dict], page_num: int) -> None:
        nonlocal total_written
        from app.repositories import snapshots as snap_repo
        async with factory() as session:
            async with session.begin():
                results = await listings_repo.upsert_batch(
                    session=session,
                    source_id=source_id,
                    postings=postings,
                )
                for entity, changed in results:
                    if changed:
                        posting = next(p for p in postings if p["external_id"] == entity.external_id)
                        await snap_repo.create_from_posting(
                            session=session,
                            listing_id=entity.id,
                            posting=posting,
                            content_hash=entity.content_hash,
                        )
                total_written += len(postings)

    try:
        async with factory() as session:
            async with session.begin():
                agg = await _monitor(
                    cfg=cfg,
                    db_session=session,
                    source_id=source_id,
                    portal=portal,
                    persist_fn=persist,
                    operation_key=operation_key,
                    province_key=location_key,
                )
        final_status = "success"
    except Exception as exc:
        log.error("discovery_service: incremental_monitor falló — %s", exc)
        agg = {}
        final_status = "failed"
        from app.core.alerts import dispatch
        await dispatch(
            "run_failed", "critical",
            f"incremental_monitor falló: {exc}",
            {"run_id": run_id, "portal": portal},
        )

    stats = {**agg, "listings_written": total_written}
    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    log.info(
        "discovery_service: incremental_monitor run_id=%d status=%s checked=%d found=%d written=%d",
        run_id, final_status, agg.get("segments_checked", 0), agg.get("listings_found", 0), total_written,
    )
    return {"run_id": run_id, "status": final_status, **stats}
