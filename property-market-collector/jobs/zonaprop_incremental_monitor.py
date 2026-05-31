#!/usr/bin/env python3
"""
jobs/zonaprop_incremental_monitor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Monitoreo incremental diario de segmentos de mercado Zonaprop.

Para cada segmento hoja activo:
1. Consulta el total_count actual vía API.
2. Compara con el snapshot anterior.
3. Si el delta es menor al umbral → omitir.
4. Si el delta es moderado → scan parcial (primeras N páginas).
5. Si el delta es mayor → scan completo del segmento.
6. Guarda un nuevo snapshot.

Las publicaciones nuevas se persisten en listing_entities.

Uso:
    python jobs/zonaprop_incremental_monitor.py
    python jobs/zonaprop_incremental_monitor.py --operations compra
    python jobs/zonaprop_incremental_monitor.py --provinces capital_federal
    python jobs/zonaprop_incremental_monitor.py --dry-run
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

from discovery.zonaprop.segment_config import load_config  # noqa: E402
from discovery.zonaprop.incremental_monitor import run_incremental_monitor  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_incremental_monitor")

_PORTAL = "zonaprop"
_SOURCE_CODE = "zonaprop"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Monitoreo incremental de segmentos Zonaprop."
    )
    p.add_argument("--operations", nargs="+", default=None)
    p.add_argument("--provinces", nargs="+", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Sin escritura a DB — solo loguear counts y acciones")
    return p.parse_args()


async def _get_source_id(session, source_code: str) -> int:
    from sqlalchemy import select
    from app.db.models import MarketSource
    result = await session.execute(
        select(MarketSource).where(MarketSource.code == source_code)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise RuntimeError(
            f"Source '{source_code}' no encontrado. Ejecutar seed primero."
        )
    return source.id


async def _run(args: argparse.Namespace) -> int:
    from app.db.session import get_async_session_factory
    from app.repositories import listings as listing_repo

    cfg = load_config(Path(args.config) if args.config else None)
    factory = get_async_session_factory()

    async with factory() as session:
        source_id = await _get_source_id(session, _SOURCE_CODE)

    op_filter = args.operations[0] if args.operations and len(args.operations) == 1 else None
    prov_filter = args.provinces[0] if args.provinces and len(args.provinces) == 1 else None

    log.info("=" * 70)
    log.info("Zonaprop incremental monitor — iniciando")
    log.info("  minor_delta_ratio : %.0f%%", cfg.minor_delta_ratio * 100)
    log.info("  major_delta_ratio : %.0f%%", cfg.major_delta_ratio * 100)
    log.info("  partial_pages     : %d", cfg.partial_scan_pages)
    log.info("  source_id         : %d", source_id)
    log.info("  dry_run           : %s", args.dry_run)
    log.info("=" * 70)

    start_time = time.monotonic()
    total_written = 0

    if args.dry_run:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_written
            total_written += len(postings)

        async with factory() as session:
            agg = await run_incremental_monitor(
                cfg=cfg,
                db_session=session,
                source_id=source_id,
                portal=_PORTAL,
                persist_fn=_persist,
                operation_key=op_filter,
                province_key=prov_filter,
            )
            # dry-run: no commit
    else:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_written
            from app.repositories import snapshots as snap_repo
            async with factory() as sess:
                async with sess.begin():
                    results = await listing_repo.upsert_batch(
                        session=sess,
                        source_id=source_id,
                        postings=postings,
                    )
                    for entity, is_new, needs_snapshot in results:
                        if needs_snapshot:
                            posting = next(p for p in postings if p["external_id"] == entity.external_id)
                            await snap_repo.create_from_posting(
                                session=sess,
                                listing_id=entity.id,
                                posting=posting,
                                content_hash=entity.content_hash,
                            )
                    total_written += len(postings)

        async with factory() as session:
            async with session.begin():
                agg = await run_incremental_monitor(
                    cfg=cfg,
                    db_session=session,
                    source_id=source_id,
                    portal=_PORTAL,
                    persist_fn=_persist,
                    operation_key=op_filter,
                    province_key=prov_filter,
                )

    # Filtro adicional si se pasaron múltiples operaciones/provincias
    # (ya aplicado dentro de run_incremental_monitor via get_leaf_segments)

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

    log.info("=" * 70)
    log.info("RESUMEN")
    log.info("  segmentos revisados  : %d", agg["segments_checked"])
    log.info("  sin cambios          : %d", agg["segments_skipped"])
    log.info("  scan parcial         : %d", agg["segments_partial_scan"])
    log.info("  scan completo        : %d", agg["segments_full_scan"])
    log.info("  publicaciones nuevas : %d", agg["listings_found"])
    log.info("  escritas en DB       : %d", total_written)
    log.info("  snapshots guardados  : %d", agg["snapshots_saved"])
    log.info("  duración             : %s", elapsed_str)
    log.info("  dry_run              : %s", args.dry_run)
    log.info("=" * 70)

    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
