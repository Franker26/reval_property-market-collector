"""Endpoints para collection_errors."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories import collection_errors as errors_repo

router = APIRouter(prefix="/errors", tags=["errors"])


@router.get("")
async def list_errors(
    source_id: Optional[int] = None,
    run_id: Optional[int] = None,
    error_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    errs = await errors_repo.list_recent(
        db,
        source_id=source_id,
        run_id=run_id,
        error_type=error_type,
        limit=limit,
        offset=offset,
    )
    return [_error_dict(e) for e in errs]


def _error_dict(e) -> dict:
    return {
        "id": e.id,
        "run_id": e.run_id,
        "source_id": e.source_id,
        "listing_id": e.listing_id,
        "external_id": e.external_id,
        "url": e.url,
        "error_type": e.error_type,
        "error_message": e.error_message,
        "http_status": e.http_status,
        "retryable": e.retryable,
        "failed_at": e.failed_at.isoformat() if e.failed_at else None,
    }
