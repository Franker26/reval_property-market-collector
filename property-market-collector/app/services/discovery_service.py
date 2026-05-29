"""
Servicio de discovery: orquesta las 3 fases del pipeline de segmentos.
Cada función crea un collection_run para trazabilidad.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.session import get_async_session_factory
from app.repositories import collection_runs as runs_repo
from app.repositories import listings as listings_repo
from app.repositories import market_segments as seg_repo
from app.repositories import sources as sources_repo
from app.repositories import url_discovery_segment_runs as run_repo

log = logging.getLogger(__name__)


# ── Fase 1: Segment Discovery (semanal) ───────────────────────────────────────


async def run_segment_discovery(
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    operations: Optional[list[str]] = None,
    locations: Optional[list[str]] = None,
) -> dict:
    """
    Construye el árbol adaptativo de segmentos precio × superficie.
    Desactiva los segmentos previos y persiste los nuevos con su snapshot inicial.
    """
    from discovery.zonaprop.segment_config import load_config
    from discovery.zonaprop.segment_discovery import run_segment_discovery as _discover

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
        )
        await session.commit()
        run_id = run.id

    async with factory() as session:
        async with session.begin():
            await seg_repo.deactivate_portal_segments(session, portal)

    leaf_count = 0
    oversized_count = 0

    async def on_leaf(node) -> None:
        nonlocal leaf_count, oversized_count
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
    except Exception as exc:
        log.error("discovery_service: segment_discovery falló — %s", exc)
        final_status = "failed"

    stats = {"leaf_count": leaf_count, "oversized_count": oversized_count}
    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    async with factory() as session:
        async with session.begin():
            new_runs = await seg_repo.sync_pending_segment_runs(session, portal)
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
) -> dict:
    """
    Consume runs pendientes de url_discovery_segment_runs hasta alcanzar stop_at.
    Cada segmento se procesa completo; el chequeo de tiempo ocurre entre segmentos.
    """
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

    stats: dict = {"processed": 0, "complete": 0, "stopped_early": 0, "failed": 0}

    for run in pending:
        if datetime.now(timezone.utc) >= stop_at.astimezone(timezone.utc):
            log.info("url_discovery_window: ventana horaria alcanzada — deteniendo")
            break

        async with factory() as session:
            async with session.begin():
                await run_repo.mark_started(session, run.id)

        total_found = 0
        changed_count = 0

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

        try:
            seg_stats = await _discover([run.segment], persist_fn=persist)
            per_seg = seg_stats.get("per_segment", [{}])
            stopped_early = per_seg[0].get("stopped_early", False) if per_seg else False
            pages_ok = per_seg[0].get("pages_ok", 0) if per_seg else 0

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

    return {"status": "done", **stats}


# ── Fase 2b: URL Discovery (sin ventana, bajo demanda) ────────────────────────


async def run_url_discovery(
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    operation_key: Optional[str] = None,
    location_key: Optional[str] = None,
    max_pages_per_segment: Optional[int] = None,
) -> dict:
    """
    Pagina los segmentos hoja y persiste publicaciones en listing_entities.
    """
    from discovery.zonaprop.url_discovery import run_url_discovery as _discover

    factory = get_async_session_factory()

    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            return {"error": f"source '{source_code}' not found"}
        source_id = source.id
        run = await runs_repo.start(
            session,
            run_type="url_discovery",
            source_id=source_id,
            params={"portal": portal, "operation_key": operation_key, "location_key": location_key},
        )
        await session.commit()
        run_id = run.id

        segments = await seg_repo.get_leaf_segments(
            session, portal=portal, operation_key=operation_key, province_key=location_key
        )

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
        agg = await _discover(segments, persist_fn=persist, max_pages_per_segment=max_pages_per_segment)
        final_status = "success" if agg["total_found"] > 0 else "partial"
    except Exception as exc:
        log.error("discovery_service: url_discovery falló — %s", exc)
        agg = {"total_found": 0, "segments_processed": 0, "segments_failed": 0}
        final_status = "failed"

    stats = {**agg, "listings_written": total_written}
    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    log.info(
        "discovery_service: url_discovery run_id=%d status=%s found=%d written=%d",
        run_id, final_status, agg["total_found"], total_written,
    )
    return {"run_id": run_id, "status": final_status, **stats}


# ── Fase 3: Incremental Monitor (diario) ─────────────────────────────────────


async def run_incremental_monitor(
    portal: str = "zonaprop",
    source_code: str = "zonaprop",
    operation_key: Optional[str] = None,
    location_key: Optional[str] = None,
) -> dict:
    """
    Compara total_count actual con el snapshot anterior y rescanea si cambió.
    """
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

    stats = {**agg, "listings_written": total_written}
    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    log.info(
        "discovery_service: incremental_monitor run_id=%d status=%s checked=%d found=%d written=%d",
        run_id, final_status, agg.get("segments_checked", 0), agg.get("listings_found", 0), total_written,
    )
    return {"run_id": run_id, "status": final_status, **stats}
