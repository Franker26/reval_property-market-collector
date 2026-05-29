#!/usr/bin/env python3
"""
jobs/zonaprop_url_discovery.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Extrae URLs de publicaciones de Zonaprop paginando los segmentos hoja
almacenados en la base de datos.

Carga todos los segmentos hoja activos del portal y para cada uno
pagina la API hasta agotar los resultados. Las publicaciones nuevas
se insertan en listing_entities; las existentes actualizan last_seen_at.

Uso:
    python jobs/zonaprop_url_discovery.py
    python jobs/zonaprop_url_discovery.py --operations compra
    python jobs/zonaprop_url_discovery.py --provinces capital_federal
    python jobs/zonaprop_url_discovery.py --dry-run   # sin escribir a DB
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
        description="Extrae URLs de publicaciones de Zonaprop desde segmentos hoja."
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

    factory = get_async_session_factory()

    async with factory() as session:
        source_id = await _get_source_id(session, _SOURCE_CODE)
        segments = await seg_repo.get_leaf_segments(
            session,
            portal=_PORTAL,
            operation_key=args.operations[0] if args.operations and len(args.operations) == 1 else None,
            province_key=args.provinces[0] if args.provinces and len(args.provinces) == 1 else None,
        )

    # Filtrar por múltiples operaciones/provincias si corresponde
    if args.operations and len(args.operations) > 1:
        segments = [s for s in segments if s.operation_key in args.operations]
    if args.provinces and len(args.provinces) > 1:
        segments = [s for s in segments if s.province_key in args.provinces]

    if not segments:
        log.warning("No hay segmentos hoja activos para los filtros dados.")
        return 1

    log.info("=" * 70)
    log.info("Zonaprop URL discovery — iniciando")
    log.info("  segmentos hoja : %d", len(segments))
    log.info("  source_id      : %d", source_id)
    log.info("  max_pages      : %s", args.max_pages or "sin límite")
    log.info("  dry_run        : %s", args.dry_run)
    log.info("=" * 70)

    start_time = time.monotonic()
    total_written = 0
    total_skipped = 0

    if args.dry_run:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_written
            total_written += len(postings)
            log.debug("dry_run: %d postings en página %d", len(postings), page_num)

        agg = await run_url_discovery(
            segments=segments,
            persist_fn=_persist,
            max_pages_per_segment=args.max_pages,
        )
    else:
        async def _persist(postings: list[dict], page_num: int) -> None:
            nonlocal total_written, total_skipped
            async with factory() as session:
                async with session.begin():
                    for p in postings:
                        entity = await listing_repo.upsert(
                            session=session,
                            source_id=source_id,
                            external_id=p["external_id"],
                            canonical_url=p.get("canonical_url"),
                            operation_type=p.get("operation_type"),
                            property_type=p.get("property_type"),
                        )
                        # Actualizar segment_id si cambió o era None
                        seg_db_id = p.get("segment_db_id")
                        if seg_db_id and entity.segment_id != seg_db_id:
                            entity.segment_id = seg_db_id
                    total_written += len(postings)

        agg = await run_url_discovery(
            segments=segments,
            persist_fn=_persist,
            max_pages_per_segment=args.max_pages,
        )

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

    log.info("=" * 70)
    log.info("RESUMEN")
    log.info("  segmentos procesados : %d", agg["segments_processed"])
    log.info("  segmentos fallidos   : %d", agg["segments_failed"])
    log.info("  publicaciones        : %d", agg["total_found"])
    log.info("  escritas en DB       : %d", total_written)
    log.info("  duración             : %s", elapsed_str)
    log.info("  dry_run              : %s", args.dry_run)
    log.info("=" * 70)

    return 0 if agg["total_found"] > 0 else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
