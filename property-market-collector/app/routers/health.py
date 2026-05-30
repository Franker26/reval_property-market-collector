"""GET /health/discovery — estado operativo en tiempo real del pipeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import get_all_limiter_states
from app.db.session import get_async_session_factory
from app.repositories import collection_errors as errors_repo
from app.repositories import collection_runs as runs_repo
from app.repositories import url_discovery_segment_runs as seg_run_repo

router = APIRouter(tags=["health"])


async def _get_session() -> AsyncSession:
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


@router.get("/health/discovery")
async def discovery_health():
    """
    Estado operativo consolidado del pipeline de discovery.

    Devuelve sin autenticación para permitir monitoreo externo simple.
    """
    factory = get_async_session_factory()
    now = datetime.now(timezone.utc)

    async with factory() as session:
        active_run = await runs_repo.get_active(session)
        last_run = await runs_repo.get_last_completed(session)
        segment_counts = await seg_run_repo.count_by_status(session)
        errors_last_hour = await errors_repo.count_by_type_since(
            session, since=now - timedelta(hours=1)
        )
        last_error = await errors_repo.get_last(session)

    active_run_data = None
    if active_run:
        duration_so_far = (now - active_run.started_at.replace(tzinfo=timezone.utc)).total_seconds()
        active_run_data = {
            "id": active_run.id,
            "run_type": active_run.run_type,
            "started_at": active_run.started_at.isoformat() if active_run.started_at else None,
            "duration_so_far_s": round(duration_so_far),
            "params": active_run.params_json,
        }

    last_run_data = None
    if last_run:
        last_run_data = {
            "id": last_run.id,
            "run_type": last_run.run_type,
            "status": last_run.status,
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
            "duration_seconds": float(last_run.duration_seconds) if last_run.duration_seconds else None,
            "stats": last_run.stats_json,
        }

    last_error_data = None
    if last_error:
        last_error_data = {
            "error_type": last_error.error_type,
            "http_status": last_error.http_status,
            "message": last_error.error_message,
            "failed_at": last_error.failed_at.isoformat() if last_error.failed_at else None,
            "retryable": last_error.retryable,
        }

    total_segments = sum(segment_counts.values())
    complete = segment_counts.get("complete", 0)
    progress_pct = round(complete / total_segments * 100, 1) if total_segments > 0 else None

    return {
        "timestamp": now.isoformat(),
        "active_run": active_run_data,
        "last_completed_run": last_run_data,
        "segment_progress": {
            "pending": segment_counts.get("pending", 0),
            "running": segment_counts.get("running", 0),
            "complete": complete,
            "failed": segment_counts.get("failed", 0),
            "total": total_segments,
            "progress_pct": progress_pct,
        },
        "rate_limiters": get_all_limiter_states(),
        "recent_errors": {
            "last_1h": errors_last_hour,
            "total_last_1h": sum(errors_last_hour.values()),
        },
        "last_error": last_error_data,
    }
