"""Repositorio para market_sources."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketSource


async def get_by_code(session: AsyncSession, code: str) -> Optional[MarketSource]:
    result = await session.execute(select(MarketSource).where(MarketSource.code == code))
    return result.scalar_one_or_none()


async def get_all(session: AsyncSession) -> list[MarketSource]:
    result = await session.execute(select(MarketSource).where(MarketSource.enabled.is_(True)))
    return list(result.scalars().all())


async def get_by_id(session: AsyncSession, source_id: int) -> Optional[MarketSource]:
    return await session.get(MarketSource, source_id)
