"""Seed inicial de market_sources."""
from __future__ import annotations

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.db.models import MarketSource

log = logging.getLogger(__name__)

_INITIAL_SOURCES = [
    {"code": "zonaprop",     "name": "Zonaprop",      "base_url": "https://www.zonaprop.com.ar"},
    {"code": "argenprop",    "name": "Argenprop",     "base_url": "https://www.argenprop.com"},
    {"code": "mercadolibre", "name": "Mercado Libre", "base_url": "https://inmuebles.mercadolibre.com.ar"},
]


async def seed_sources(session: AsyncSession) -> None:
    for src in _INITIAL_SOURCES:
        stmt = (
            insert(MarketSource)
            .values(**src, enabled=True)
            .on_conflict_do_nothing(index_elements=["code"])
        )
        await session.execute(stmt)
    await session.commit()
    log.info("seed: market_sources OK (%d fuentes)", len(_INITIAL_SOURCES))
