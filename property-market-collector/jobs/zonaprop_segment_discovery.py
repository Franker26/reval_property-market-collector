#!/usr/bin/env python3
"""
jobs/zonaprop_segment_discovery.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Construye el árbol adaptativo de segmentos de mercado para Zonaprop.

Divide el espacio precio × superficie por operación y provincia hasta
que cada segmento hoja tenga total_count <= max_results_per_segment.

Los segmentos descubiertos se persisten en la tabla market_segments.
Antes de ejecutar, los segmentos activos previos se marcan como
inactivos (los nuevos los reemplazan).

Uso:
    python jobs/zonaprop_segment_discovery.py
    python jobs/zonaprop_segment_discovery.py --operations compra alquiler
    python jobs/zonaprop_segment_discovery.py --provinces capital_federal cordoba
    python jobs/zonaprop_segment_discovery.py --dry-run   # sin escribir a DB
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from discovery.zonaprop.segment_config import load_config, SegmentConfig  # noqa: E402
from discovery.zonaprop.segment_discovery import run_segment_discovery, SegmentNode  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_segment_discovery")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Construye el árbol de segmentos de mercado para Zonaprop."
    )
    p.add_argument(
        "--operations", nargs="+", default=None,
        help="Filtrar solo estas operaciones (ej: compra alquiler)",
    )
    p.add_argument(
        "--provinces", nargs="+", default=None,
        help="Filtrar solo estas provincias (ej: capital_federal cordoba)",
    )
    p.add_argument(
        "--config", default=None,
        help="Path alternativo al YAML de configuración",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Ejecutar sin escribir a la base de datos",
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config) if args.config else None)

    # Aplicar filtros de subconjunto
    if args.operations:
        cfg.operations = {k: v for k, v in cfg.operations.items() if k in args.operations}
    if args.provinces:
        cfg.locations = {k: v for k, v in cfg.locations.items() if k in args.provinces}

    if not cfg.operations:
        log.error("No hay operaciones configuradas. Verificar config o parámetros --operations.")
        return 1
    if not cfg.locations:
        log.error("No hay provincias configuradas. Verificar config o parámetros --provinces.")
        return 1

    log.info("=" * 70)
    log.info("Zonaprop segment discovery — iniciando")
    log.info("  operaciones : %s", list(cfg.operations.keys()))
    log.info("  provincias  : %d configuradas", len(cfg.locations))
    log.info("  umbral      : %d resultados/segmento", cfg.max_results_per_segment)
    log.info("  max_depth   : %d", cfg.max_depth)
    log.info("  dry_run     : %s", args.dry_run)
    log.info("=" * 70)

    start_time = time.monotonic()
    leaf_count = 0
    oversized_count = 0

    if args.dry_run:
        # Sin DB: solo ejecutar y loguear
        async def _on_leaf(node: SegmentNode) -> None:
            nonlocal leaf_count, oversized_count
            leaf_count += 1
            if node.is_oversized:
                oversized_count += 1

        leaves = await run_segment_discovery(cfg, portal="zonaprop", on_leaf_found=_on_leaf)
    else:
        from app.db.session import get_async_session_factory
        from app.repositories import market_segments as seg_repo

        factory = get_async_session_factory()

        # Marcar segmentos activos previos como inactivos
        async with factory() as session:
            async with session.begin():
                deactivated = await seg_repo.deactivate_portal_segments(session, portal="zonaprop")
                log.info("segment_discovery: %d segmentos previos marcados inactivos", deactivated)

        # Construir árbol con persistencia incremental de hojas
        async def _on_leaf(node: SegmentNode) -> None:
            nonlocal leaf_count, oversized_count
            leaf_count += 1
            if node.is_oversized:
                oversized_count += 1

            async with factory() as session:
                async with session.begin():
                    db_seg = await seg_repo.upsert_segment(
                        session=session,
                        portal=node.portal,
                        operation_key=node.operation_key,
                        operation_value=node.operation_value,
                        province_key=node.province_key,
                        province_value=node.province_value,
                        price_min=node.price_min,
                        price_max=node.price_max,
                        surface_min=node.surface_min,
                        surface_max=node.surface_max,
                        total_count=node.total_count,
                        depth=node.depth,
                        parent_id=node.parent_db_id,
                        is_leaf=True,
                        is_oversized=node.is_oversized,
                    )
                    node.db_id = db_seg.id

                    # Snapshot inicial
                    await seg_repo.save_snapshot(
                        session=session,
                        segment_id=db_seg.id,
                        total_count=node.total_count or 0,
                        price_min=node.price_min,
                        price_max=node.price_max,
                        surface_min=node.surface_min,
                        surface_max=node.surface_max,
                    )

        leaves = await run_segment_discovery(cfg, portal="zonaprop", on_leaf_found=_on_leaf)

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

    log.info("=" * 70)
    log.info("RESUMEN")
    log.info("  hojas descubiertas  : %d", leaf_count)
    log.info("  hojas oversized     : %d", oversized_count)
    log.info("  duración            : %s", elapsed_str)
    log.info("  dry_run             : %s", args.dry_run)
    log.info("=" * 70)

    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
