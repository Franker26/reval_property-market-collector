"""Endpoints para collection_runs."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories import collection_runs as runs_repo

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
async def list_runs(
    source_id: Optional[int] = None,
    run_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    runs = await runs_repo.list_recent(db, source_id=source_id, run_type=run_type, limit=limit, offset=offset)
    return [_run_dict(r) for r in runs]


@router.get("/{run_id}")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await runs_repo.get_by_id(db, run_id)
    if not run:
        raise HTTPException(404, "Run no encontrado")
    return _run_dict(run)


def _run_dict(r) -> dict:
    return {
        "id": r.id,
        "source_id": r.source_id,
        "run_type": r.run_type,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "duration_seconds": float(r.duration_seconds) if r.duration_seconds is not None else None,
        "params_json": r.params_json,
        "stats_json": r.stats_json,
    }
