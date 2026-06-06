"""
Endpoints para triggear manualmente las fases del pipeline de discovery.

Regla: el trigger manual requiere que el scheduler correspondiente esté pausado.
De esta forma siempre hay un único flujo de ejecución, sin solapamientos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])


# ── Scheduler guard ───────────────────────────────────────────────────────────

def _all_paused(*job_ids: str) -> bool:
    """True si todos los jobs dados están pausados (o no existen)."""
    try:
        from app.services.scheduler_service import get_scheduler
        sched = get_scheduler()
        return all(
            sched.get_job(jid) is None or sched.get_job(jid).next_run_time is None
            for jid in job_ids
        )
    except Exception:
        return True  # scheduler no disponible → permitir trigger manual


# ── Request models ────────────────────────────────────────────────────────────

class SegmentDiscoveryRequest(BaseModel):
    operations: Optional[list[str]] = None
    locations: Optional[list[str]] = None


class MonitorRequest(BaseModel):
    operation_key: Optional[str] = None
    location_key: Optional[str] = None


# ── Background runners ────────────────────────────────────────────────────────

async def _bg_segment_discovery(
    operations: Optional[list[str]],
    locations: Optional[list[str]],
) -> None:
    try:
        from app.services.discovery_service import run_segment_discovery
        result = await run_segment_discovery(operations=operations, locations=locations)
        log.info("bg segment_discovery finalizado: %s", result)
    except Exception as exc:
        log.error("bg segment_discovery error: %s", exc)


async def _bg_url_discovery_manual() -> None:
    """Trigger manual: usa la misma cola que el scheduler, con ventana de 24h."""
    try:
        from app.services.discovery_service import run_url_discovery_window
        stop_at = datetime.now(timezone.utc) + timedelta(hours=24)
        result = await run_url_discovery_window(stop_at=stop_at, mode="manual")
        log.info("bg url_discovery manual finalizado: %s", result)
    except Exception as exc:
        log.error("bg url_discovery manual error: %s", exc)


async def _bg_incremental_monitor(
    operation_key: Optional[str],
    location_key: Optional[str],
) -> None:
    try:
        from app.services.discovery_service import run_incremental_monitor
        result = await run_incremental_monitor(
            operation_key=operation_key,
            location_key=location_key,
        )
        log.info("bg incremental_monitor finalizado: %s", result)
    except Exception as exc:
        log.error("bg incremental_monitor error: %s", exc)


async def _bg_build_location_normalization() -> None:
    try:
        from jobs.build_location_normalization import run
        await run(mode="incremental", batch_size=500, source_id=None, dry_run=False)
        log.info("bg build_location_normalization finalizado")
    except Exception as exc:
        log.error("bg build_location_normalization error: %s", exc)


async def _bg_build_market_facts() -> None:
    try:
        from jobs.build_market_facts import run
        await run(mode="incremental", batch_size=500, source_id=None, dry_run=False)
        log.info("bg build_market_facts finalizado")
    except Exception as exc:
        log.error("bg build_market_facts error: %s", exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/segment-discovery")
async def trigger_segment_discovery(
    body: SegmentDiscoveryRequest,
    background_tasks: BackgroundTasks,
):
    """
    Inicia el discovery de segmentos precio × superficie.

    Requiere que el scheduler 'weekly_segment_discovery' esté pausado.
    Seguir progreso en GET /runs?run_type=segment_discovery.
    """
    if not _all_paused("weekly_segment_discovery"):
        raise HTTPException(
            409,
            "El scheduler 'weekly_segment_discovery' está activo. "
            "Pausalo antes de triggerear manualmente.",
        )
    background_tasks.add_task(_bg_segment_discovery, body.operations, body.locations)
    return {
        "status": "started",
        "message": "Segment discovery iniciado en background.",
        "monitor_url": "GET /runs?run_type=segment_discovery",
        "operations": body.operations or "all",
        "locations": body.locations or "all",
    }


@router.post("/url-discovery")
async def trigger_url_discovery(background_tasks: BackgroundTasks):
    """
    Procesa la cola de segmentos pendientes (zonaprop_segment_scan_queue).

    Requiere que los schedulers 'weekday_url_discovery' y 'sunday_url_discovery'
    estén pausados. La ventana manual dura hasta 24h.
    """
    if not _all_paused("weekday_url_discovery", "sunday_url_discovery"):
        raise HTTPException(
            409,
            "El scheduler de url_discovery está activo. "
            "Pausá 'weekday_url_discovery' y 'sunday_url_discovery' antes de triggerear manualmente.",
        )
    background_tasks.add_task(_bg_url_discovery_manual)
    return {
        "status": "started",
        "message": "URL discovery manual iniciado. Procesa cola pendiente con ventana de 24h.",
        "monitor_url": "GET /runs?run_type=url_discovery_window",
    }


@router.post("/incremental-monitor")
async def trigger_incremental_monitor(
    body: MonitorRequest,
    background_tasks: BackgroundTasks,
):
    """Monitoreo incremental: compara counts y rescanea segmentos que cambiaron."""
    background_tasks.add_task(_bg_incremental_monitor, body.operation_key, body.location_key)
    return {
        "status": "started",
        "message": "Incremental monitor iniciado en background.",
        "monitor_url": "GET /runs?run_type=incremental_monitor",
    }


@router.post("/build-location-normalization")
async def trigger_build_location_normalization(background_tasks: BackgroundTasks):
    """Ejecuta build_location_normalization incremental en background."""
    if not _all_paused("build_location_normalization_6h"):
        raise HTTPException(
            409,
            "El scheduler 'build_location_normalization_6h' está activo. "
            "Pausalo antes de triggerear manualmente.",
        )
    background_tasks.add_task(_bg_build_location_normalization)
    return {
        "status": "started",
        "message": "build_location_normalization iniciado en background.",
    }


@router.post("/build-market-facts")
async def trigger_build_market_facts(background_tasks: BackgroundTasks):
    """Ejecuta build_market_facts incremental en background."""
    if not _all_paused("build_market_facts_6h"):
        raise HTTPException(
            409,
            "El scheduler 'build_market_facts_6h' está activo. "
            "Pausalo antes de triggerear manualmente.",
        )
    background_tasks.add_task(_bg_build_market_facts)
    return {
        "status": "started",
        "message": "build_market_facts iniciado en background.",
    }


@router.post("/scheduler/pause-job/{job_id}")
async def pause_scheduler_job(job_id: str):
    """Pausa un job del scheduler por ID."""
    sched = _get_sched()
    job = sched.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job '{job_id}' no encontrado")
    job.pause()
    return {"status": "paused", "job_id": job_id, "next_run": None}


@router.post("/scheduler/resume-job/{job_id}")
async def resume_scheduler_job(job_id: str):
    """Reactiva un job previamente pausado."""
    sched = _get_sched()
    job = sched.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job '{job_id}' no encontrado")
    job.resume()
    next_run = job.next_run_time
    return {
        "status": "resumed",
        "job_id": job_id,
        "next_run": next_run.isoformat() if next_run else None,
    }


@router.get("/scheduler/jobs")
async def list_scheduler_jobs():
    """Lista todos los jobs del scheduler con su próxima ejecución y estado."""
    sched = _get_sched()
    return [
        {
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "paused": job.next_run_time is None,
        }
        for job in sched.get_jobs()
    ]


def _get_sched():
    from app.services.scheduler_service import get_scheduler
    return get_scheduler()


@router.get("/segments")
async def list_segments(
    portal: str = "zonaprop",
    operation_key: Optional[str] = None,
    location_key: Optional[str] = None,
    only_leaves: bool = True,
    limit: int = 100,
    offset: int = 0,
):
    """Lista los segmentos de mercado activos."""
    from sqlalchemy import select
    from app.db.models import ZonapropSegment
    from app.db.session import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        stmt = select(ZonapropSegment).where(
            ZonapropSegment.portal == portal,
            ZonapropSegment.status == "active",
        )
        if only_leaves:
            stmt = stmt.where(ZonapropSegment.is_leaf == True)  # noqa: E712
        if operation_key:
            stmt = stmt.where(ZonapropSegment.operation_key == operation_key)
        if location_key:
            stmt = stmt.where(ZonapropSegment.province_key == location_key)
        stmt = stmt.order_by(ZonapropSegment.id).limit(limit).offset(offset)
        result = await session.execute(stmt)
        segments = list(result.scalars().all())

    return [
        {
            "id": s.id,
            "operation_key": s.operation_key,
            "location_key": s.province_key,
            "price_min": float(s.price_min),
            "price_max": float(s.price_max),
            "surface_min": float(s.surface_min),
            "surface_max": float(s.surface_max),
            "total_count": s.total_count,
            "depth": s.depth,
            "is_leaf": s.is_leaf,
            "is_oversized": s.is_oversized,
            "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        }
        for s in segments
    ]
