"""Endpoints para market_sources."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories import sources as sources_repo

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
async def list_sources(db: AsyncSession = Depends(get_db)):
    srcs = await sources_repo.get_all(db)
    return [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "base_url": s.base_url,
            "enabled": s.enabled,
        }
        for s in srcs
    ]
