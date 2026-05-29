#!/usr/bin/env python3
"""
jobs/zonaprop_url_discovery.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Extrae datos de publicaciones de Zonaprop paginando los segmentos hoja
almacenados en la base de datos.

Por cada batch de 30 postings:
  - CASO A: listing nuevo → INSERT en listing_entities + snapshot inicial
  - CASO B: sin cambios → solo actualiza last_seen_at
  - CASO C: cambió algo → actualiza listing_entities + nuevo snapshot

Al finalizar cada segmento completo (sin stopped_early):
  - CASO D: listings que no aparecieron → marcados como 'offline' + snapshot

Uso:
    python jobs/zonaprop_url_discovery.py
    python jobs/zonaprop_url_discovery.py --operations compra
    python jobs/zonaprop_url_discovery.py --provinces capital_federal
    python jobs/zonaprop_url_discovery.py --max-pages 2  # para pruebas
    python jobs/zonaprop_url_discovery.py --dry-run      # sin escritura a DB
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from discovery.zonaprop.url_discovery import run_url_discovery  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_url_discovery")

_PORTAL = "zonaprop"
_SOURCE_CODE = "zonaprop"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extrae publicaciones de Zonaprop desde segmentos hoja."
    )
    p.add_argument("--operations", nargs="+", default=None)
    p.add_argument("--provinces", nargs="+", default=None)
    p.add_argument("--max-pages", type=int, default=None,
                   help="Límite de páginas por segmento (default: sin límite)")
    p.add_argument("--dry-run", action="store_true",
                   help="Sin escritura a DB — solo loguear")
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
            f"Source '{source_code}' no encontrado en market_sources. "
            "Verificar que el seed se ejecutó correctamente."
        )
    return source.id


async def _run(args: argparse.Namespace) -> int:
    from app.db.session import get_async_session_factory
    from app.repositories import market_segments as seg_repo
    from app.repositories import listings as listing_repo
    from app.repositories import snapshots as snap_repo

    factory = get_async_session_factory()

    async with factory() as session:
        source_id = await _get_source_id(session, _SOURCE_CODE)
        segments = await seg_repo.get_leaf_segments(
            session,
            portal=_PORTAL,
            operation_key=args.operations[0] if args.operations and len(args.operations) == 1 else None,
            province_key=args.provinces[0] if args.provinces and len(args.provinces) == 1 else None,
        )

    if args.operations and len(args.operations) > 1:
        segments = [s for s in segments if s.operation_key in args.operations]
    if args.provinces and len(args.provinces) > 1:
        segments = [s for s in segments if s.province_key in args.provinces]

    if not segments:
        log.warning("No hay segmentos hoja activos para los filtros dados.")
        return 1

    run_started_at = datetime.utcnow()

    log.info("=" * 70)
    log.info("Zonaprop URL discovery — iniciando")
    log.info("  segmentos hoja : %d", len(segments))
    log.info("  source_id      : %d", source_id)
    log.info("  max_pages      : %s", args.max_pages or "sin límite")
    log.info("  dry_run        : %s", args.dry_run)
    log.info("=" * 70)

    start_time = time.monotonic()
    total_new = 0
    total_changed = 0
    total_touched = 0
    total_offline = 0

    if args.dry_run:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_touched
            total_touched += len(postings)
            log.debug("dry_run: %d postings en página %d", len(postings), page_num)

        agg = await run_url_discovery(
            segments=segments,
            persist_fn=_persist,
            max_pages_per_segment=args.max_pages,
        )
    else:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_new, total_changed, total_touched
            async with factory() as session:
                async with session.begin():
                    results = await listing_repo.upsert_batch(
                        session=session,
                        source_id=source_id,
                        postings=postings,
                    )
                    for entity, changed in results:
                        if changed:
                            if entity.first_seen_at == entity.last_seen_at:
                                total_new += 1
                            else:
                                total_changed += 1
                            await snap_repo.create_from_posting(
                                session=session,
                                listing_id=entity.id,
                                posting=next(
                                    p for p in postings
                                    if p["external_id"] == entity.external_id
                                ),
                                content_hash=entity.content_hash,
                            )
                        else:
                            total_touched += 1

        agg = await run_url_discovery(
            segments=segments,
            persist_fn=_persist,
            max_pages_per_segment=args.max_pages,
        )

        # CASO D: marcar offline los que no aparecieron en scans completos
        for seg_stats in agg.get("per_segment", []):
            if not seg_stats["stopped_early"] and seg_stats["segment_id"] is not None:
                async with factory() as session:
                    async with session.begin():
                        n = await listing_repo.mark_offline_in_segment(
                            session=session,
                            segment_id=seg_stats["segment_id"],
                            run_started_at=run_started_at,
                        )
                        total_offline += n
                        if n:
                            log.info(
                                "offline: %d listings marcados en segmento %d (%s/%s)",
                                n, seg_stats["segment_id"],
                                seg_stats["op_key"], seg_stats["loc_key"],
                            )

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

    log.info("=" * 70)
    log.info("RESUMEN")
    log.info("  segmentos procesados : %d", agg["segments_processed"])
    log.info("  segmentos fallidos   : %d", agg["segments_failed"])
    log.info("  publicaciones vistas : %d", agg["total_found"])
    log.info("  nuevas               : %d", total_new)
    log.info("  actualizadas         : %d", total_changed)
    log.info("  sin cambios          : %d", total_touched)
    log.info("  marcadas offline     : %d", total_offline)
    log.info("  duración             : %s", elapsed_str)
    log.info("  dry_run              : %s", args.dry_run)
    log.info("=" * 70)

    return 0 if agg["total_found"] > 0 else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
