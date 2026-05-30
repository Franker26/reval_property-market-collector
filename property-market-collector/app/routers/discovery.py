"""
Endpoints para triggear manualmente las fases del pipeline de discovery.

Todas las operaciones son asíncronas (background tasks):
el endpoint devuelve inmediatamente con el run_id,
y el progreso se consulta vía GET /runs/{run_id}.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])


# ── Request models ────────────────────────────────────────────────────────────


class SegmentDiscoveryRequest(BaseModel):
    operations: Optional[list[str]] = None
    locations: Optional[list[str]] = None


class UrlDiscoveryRequest(BaseModel):
    operation_key: Optional[str] = None
    location_key: Optional[str] = None
    max_pages_per_segment: Optional[int] = None


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


async def _bg_url_discovery(
    operation_key: Optional[str],
    location_key: Optional[str],
    max_pages_per_segment: Optional[int],
) -> None:
    try:
        from app.services.discovery_service import run_url_discovery
        result = await run_url_discovery(
            operation_key=operation_key,
            location_key=location_key,
            max_pages_per_segment=max_pages_per_segment,
        )
        log.info("bg url_discovery finalizado: %s", result)
    except Exception as exc:
        log.error("bg url_discovery error: %s", exc)


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


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/segment-discovery")
async def trigger_segment_discovery(
    body: SegmentDiscoveryRequest,
    background_tasks: BackgroundTasks,
):
    """
    Inicia el discovery de segmentos precio × superficie.

    Corre en background — retorna inmediatamente con el run_id.
    Seguir progreso en GET /runs/{run_id}.

    Para tests: pasar operations=["compra"] y locations=["capital_federal"]
    para limitar el scope y reducir el tiempo de ejecución.
    """
    background_tasks.add_task(_bg_segment_discovery, body.operations, body.locations)
    return {
        "status": "started",
        "message": "Segment discovery iniciado en background.",
        "monitor_url": "GET /runs?run_type=segment_discovery",
        "operations": body.operations or "all",
        "locations": body.locations or "all",
    }


@router.post("/url-discovery")
async def trigger_url_discovery(
    body: UrlDiscoveryRequest,
    background_tasks: BackgroundTasks,
):
    """
    Extrae URLs paginando los segmentos hoja existentes.
    Requiere que segment-discovery haya corrido al menos una vez.
    """
    background_tasks.add_task(
        _bg_url_discovery,
        body.operation_key,
        body.location_key,
        body.max_pages_per_segment,
    )
    return {
        "status": "started",
        "message": "URL discovery iniciado en background.",
        "monitor_url": "GET /runs?run_type=url_discovery",
    }


@router.post("/incremental-monitor")
async def trigger_incremental_monitor(
    body: MonitorRequest,
    background_tasks: BackgroundTasks,
):
    """
    Monitoreo incremental: compara counts y rescanea segmentos que cambiaron.
    """
    background_tasks.add_task(_bg_incremental_monitor, body.operation_key, body.location_key)
    return {
        "status": "started",
        "message": "Incremental monitor iniciado en background.",
        "monitor_url": "GET /runs?run_type=incremental_monitor",
    }


@router.post("/scheduler/pause-job/{job_id}")
async def pause_scheduler_job(job_id: str):
    """
    Pausa un job del scheduler por ID (ej: weekly_segment_discovery, weekday_url_discovery).
    El job queda registrado pero no se dispara hasta que se llame /resume-job.
    """
    from app.services.scheduler_service import get_scheduler
    sched = get_scheduler()
    job = sched.get_job(job_id)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(404, f"Job '{job_id}' no encontrado")
    job.pause()
    return {"status": "paused", "job_id": job_id, "next_run": None}


@router.post("/scheduler/resume-job/{job_id}")
async def resume_scheduler_job(job_id: str):
    """Reactiva un job previamente pausado."""
    from app.services.scheduler_service import get_scheduler
    sched = get_scheduler()
    job = sched.get_job(job_id)
    if job is None:
        from fastapi import HTTPException
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
    from app.services.scheduler_service import get_scheduler
    sched = get_scheduler()
    return [
        {
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "paused": job.next_run_time is None,
        }
        for job in sched.get_jobs()
    ]


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
    from app.db.models import MarketSegment
    from app.db.session import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        stmt = select(MarketSegment).where(
            MarketSegment.portal == portal,
            MarketSegment.status == "active",
        )
        if only_leaves:
            stmt = stmt.where(MarketSegment.is_leaf == True)  # noqa: E712
        if operation_key:
            stmt = stmt.where(MarketSegment.operation_key == operation_key)
        if location_key:
            stmt = stmt.where(MarketSegment.province_key == location_key)
        stmt = stmt.order_by(MarketSegment.id).limit(limit).offset(offset)
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
