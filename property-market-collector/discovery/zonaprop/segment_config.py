"""
discovery.zonaprop.segment_config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Carga la configuración de discovery de Zonaprop desde
config/discovery/zonaprop.yaml.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "discovery" / "zonaprop.yaml"
)


@dataclass
class SegmentConfig:
    """
    Configuración del discovery adaptativo por segmentos.

    *operations* y *locations* son mappings nombre → id numérico.
    El engine los trata como claves/valores genéricos sin asumir
    semántica específica del portal.
    """
    max_results_per_segment: int = 1000
    min_surface_m2: float = 0.0
    max_surface_m2: float = 10000.0
    min_price: float = 0.0
    max_price: float = 10000000.0

    surface_split_enabled: bool = True
    price_split_enabled: bool = True
    surface_split_priority: int = 1
    price_split_priority: int = 2

    max_depth: int = 20
    min_surface_range_m2: float = 5.0
    min_price_range: float = 1000.0

    minor_delta_ratio: float = 0.02
    major_delta_ratio: float = 0.10
    partial_scan_pages: int = 3

    segment_discovery_schedule: str = "weekly"

    operations: dict[str, int] = field(default_factory=dict)
    locations: dict[str, int] = field(default_factory=dict)


def load_config(path: Optional[Path] = None) -> SegmentConfig:
    p = path or _DEFAULT_CONFIG_PATH
    if not p.exists():
        log.warning("segment_config: archivo no encontrado en %s — usando valores por defecto", p)
        return SegmentConfig()

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        log.error("segment_config: pyyaml no instalado — usando valores por defecto")
        return SegmentConfig()

    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    cfg = SegmentConfig()
    scalar_fields = (
        "max_results_per_segment", "min_surface_m2", "max_surface_m2",
        "min_price", "max_price", "surface_split_enabled", "price_split_enabled",
        "surface_split_priority", "price_split_priority", "max_depth",
        "min_surface_range_m2", "min_price_range", "minor_delta_ratio",
        "major_delta_ratio", "partial_scan_pages", "segment_discovery_schedule",
    )
    for k in scalar_fields:
        if k in raw:
            setattr(cfg, k, raw[k])

    # Soportar tanto "provinces" (legado YAML) como "locations" (genérico)
    raw_locs = raw.get("locations") or raw.get("provinces", {})
    cfg.locations = {str(k): int(v) for k, v in raw_locs.items()} if raw_locs else {}

    raw_ops = raw.get("operations", {})
    cfg.operations = {str(k): int(v) for k, v in raw_ops.items()} if raw_ops else {}

    return cfg
