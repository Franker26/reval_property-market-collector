#!/usr/bin/env python3
"""
jobs/build_location_normalization.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Construye o recalcula listing_location_normalization desde listing_entities.

MVP — estrategia sin geocoding externo:
  - lat/lon disponibles → geo_status='coordinates', geo_provider='portal'
  - sin coordenadas     → geo_status='pending'  (para geocoding futuro)

Uso:
    python jobs/build_location_normalization.py                  # incremental
    python jobs/build_location_normalization.py --mode full      # recompute todo
    python jobs/build_location_normalization.py --batch-size 500
    python jobs/build_location_normalization.py --source-id 1
    python jobs/build_location_normalization.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.build_location_normalization")

_DEFAULT_BATCH_SIZE = 500


async def _get_pending_ids(session, batch_size: int, offset: int, source_id: Optional[int], full_mode: bool) -> list[int]:
    from sqlalchemy import select
    from app.db.models import ListingEntity
    from app.db.models.location_normalization import ListingLocationNormalization

    stmt = select(ListingEntity.id)

    if not full_mode:
        from sqlalchemy.orm import aliased
        lln = aliased(ListingLocationNormalization)
        stmt = (
            select(ListingEntity.id)
            .outerjoin(lln, lln.listing_id == ListingEntity.id)
            .where(
                (lln.listing_id.is_(None)) |
                (ListingEntity.updated_at > lln.updated_at)
            )
        )

    if source_id is not None:
        stmt = stmt.where(ListingEntity.source_id == source_id)

    stmt = stmt.order_by(ListingEntity.id).limit(batch_size).offset(offset)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def run(mode: str, batch_size: int, source_id: Optional[int], dry_run: bool) -> None:
    from sqlalchemy import select
    from app.db.session import get_async_session_factory
    from app.db.models import ListingEntity, Base
    from app.db.models.location_normalization import ListingLocationNormalization
    from app.repositories import location_normalization as loc_norm_repo

    factory = get_async_session_factory()
    full_mode = mode == "full"
    t0 = time.monotonic()

    log.info("=" * 60)
    log.info("build_location_normalization — iniciando")
    log.info("  mode       : %s", mode)
    log.info("  batch_size : %d", batch_size)
    log.info("  source_id  : %s", source_id or "todos")
    log.info("  dry_run    : %s", dry_run)
    log.info("=" * 60)

    # Crear tabla si no existe (para poder correr standalone)
    from app.db.session import get_async_engine
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    total_processed = 0
    total_upserted = 0
    offset = 0

    while True:
        async with factory() as session:
            ids = await _get_pending_ids(session, batch_size, offset, source_id, full_mode)

            if not ids:
                break

            result = await session.execute(
                select(ListingEntity).where(ListingEntity.id.in_(ids))
            )
            entities = list(result.scalars().all())
            # Compute location dicts while entities are still attached to the session.
            location_rows = [loc_norm_repo.compute_location(e) for e in entities]

            if not dry_run:
                await loc_norm_repo.upsert_rows(session, location_rows)
                await session.commit()
                total_upserted += len(location_rows)

        total_processed += len(ids)

        log.info(
            "PROGRESO  offset=%d  batch=%d  total=%d",
            offset, len(ids), total_processed,
        )

        if len(ids) < batch_size:
            break
        # En modo full el set no cambia, se incrementa el offset normalmente.
        # En modo incremental, cada commit retira items del set pendiente,
        # por lo que siempre se consulta desde offset=0.
        if full_mode:
            offset += batch_size

    elapsed = time.monotonic() - t0
    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  Listings procesados : %d", total_processed)
    log.info("  Filas upserted      : %d", total_upserted)
    log.info("  Duración             : %.1fs", elapsed)
    log.info("=" * 60)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Builder de listing_location_normalization."
    )
    p.add_argument(
        "--mode", choices=["incremental", "full"], default="incremental",
        help="incremental (default) o full (recompute todo).",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Listings por lote (default: {_DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--source-id", type=int, default=None,
        help="Filtrar por source_id (portal).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Sin escritura a DB — solo loguear.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    asyncio.run(run(
        mode=args.mode,
        batch_size=args.batch_size,
        source_id=args.source_id,
        dry_run=args.dry_run,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
