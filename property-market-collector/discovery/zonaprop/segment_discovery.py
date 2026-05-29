"""
discovery.zonaprop.segment_discovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Thin wrapper de Zonaprop sobre el engine genérico de segment discovery.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from discovery.engine.models import SegmentNode  # noqa: F401 — re-export para jobs
from discovery.engine.segment_discovery import run_segment_discovery as _engine_run
from discovery.zonaprop.adapter import ZonapropAdapter
from discovery.zonaprop.segment_config import SegmentConfig


async def run_segment_discovery(
    cfg: SegmentConfig,
    on_leaf_found: Optional[Callable[[SegmentNode], Awaitable[None]]] = None,
) -> list[SegmentNode]:
    """Ejecuta el discovery de segmentos de Zonaprop usando el engine genérico."""
    adapter = ZonapropAdapter()
    return await _engine_run(adapter, cfg, on_leaf_found=on_leaf_found)
