"""
discovery.zonaprop.url_discovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Thin wrapper de Zonaprop sobre el engine genérico de url discovery.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from discovery.engine.url_discovery import (
    discover_segment,
    run_url_discovery as _engine_run,
)
from discovery.zonaprop.adapter import ZonapropAdapter


async def run_url_discovery(
    segments: list,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
    max_pages_per_segment: Optional[int] = None,
) -> dict:
    """Extrae URLs de publicaciones de Zonaprop para los segmentos hoja dados."""
    adapter = ZonapropAdapter()
    return await _engine_run(
        adapter,
        segments,
        persist_fn=persist_fn,
        max_pages_per_segment=max_pages_per_segment,
    )


__all__ = ["run_url_discovery", "discover_segment"]
