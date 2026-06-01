#!/usr/bin/env python3
"""
jobs/build_market_facts.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Construye o recalcula listing_market_facts desde listing_entities + listing_snapshots.

Consulta listing_location_normalization si existe; si no, usa ubicación cruda.

Uso:
    python jobs/build_market_facts.py                  # incremental (default)
    python jobs/build_market_facts.py --mode full      # recompute todo
    python jobs/build_market_facts.py --batch-size 500
    python jobs/build_market_facts.py --source-id 1
    python jobs/build_market_facts.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
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
log = logging.getLogger("jobs.build_market_facts")

_DEFAULT_BATCH_SIZE = 500


async def _get_pending_ids(
    session,
    batch_size: int,
    offset: int,
    source_id: Optional[int],
    full_mode: bool,
) -> list[int]:
    from sqlalchemy import select
    from sqlalchemy.orm import aliased
    from app.db.models import ListingEntity
    from app.db.models.market_facts import ListingMarketFacts

    if full_mode:
        stmt = select(ListingEntity.id)
    else:
        lmf = aliased(ListingMarketFacts)
        stmt = (
            select(ListingEntity.id)
            .outerjoin(lmf, lmf.listing_id == ListingEntity.id)
            .where(
                (lmf.listing_id.is_(None)) |
                (ListingEntity.updated_at > lmf.updated_at)
            )
        )

    if source_id is not None:
        stmt = stmt.where(ListingEntity.source_id == source_id)

    stmt = stmt.order_by(ListingEntity.id).limit(batch_size).offset(offset)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def run(mode: str, batch_size: int, source_id: Optional[int], dry_run: bool) -> None:
    from sqlalchemy import select
    from app.db.session import get_async_session_factory, get_async_engine
    from app.db.models import ListingEntity, Base
    from app.db.models.listings import ListingSnapshot
    from app.db.models.location_normalization import ListingLocationNormalization
    from app.repositories.market_facts import upsert_facts_batch
    from app.services.market_facts_service import compute_facts

    factory  = get_async_session_factory()
    full_mode = mode == "full"
    now       = datetime.now(timezone.utc)
    t0        = time.monotonic()

    log.info("=" * 60)
    log.info("build_market_facts — iniciando")
    log.info("  mode       : %s", mode)
    log.info("  batch_size : %d", batch_size)
    log.info("  source_id  : %s", source_id or "todos")
    log.info("  dry_run    : %s", dry_run)
    log.info("=" * 60)

    # Crear tablas nuevas si no existen (standalone)
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    total_processed = 0
    total_upserted  = 0
    offset          = 0

    while True:
        async with factory() as session:
            ids = await _get_pending_ids(session, batch_size, offset, source_id, full_mode)

            if not ids:
                break

            # Cargar entities
            result = await session.execute(
                select(ListingEntity).where(ListingEntity.id.in_(ids))
            )
            entities = list(result.scalars().all())

            # Cargar snapshots del batch en una sola query
            result = await session.execute(
                select(ListingSnapshot).where(ListingSnapshot.listing_id.in_(ids))
            )
            all_snapshots = result.scalars().all()
            snaps_by_listing: dict[int, list] = {}
            for snap in all_snapshots:
                snaps_by_listing.setdefault(snap.listing_id, []).append(snap)

            # Cargar location normalization del batch (LEFT JOIN implícito — puede no existir)
            result = await session.execute(
                select(ListingLocationNormalization)
                .where(ListingLocationNormalization.listing_id.in_(ids))
            )
            loc_by_listing: dict[int, ListingLocationNormalization] = {
                loc.listing_id: loc for loc in result.scalars().all()
            }

        # Computar facts fuera de la sesión (función pura)
        facts_list = []
        for entity in entities:
            snapshots    = snaps_by_listing.get(entity.id, [])
            location_norm = loc_by_listing.get(entity.id)
            facts = compute_facts(entity, snapshots, location_norm, now)
            facts_list.append(facts)

        total_processed += len(entities)

        if not dry_run:
            async with factory() as session:
                await upsert_facts_batch(session, facts_list)
                await session.commit()
            total_upserted += len(facts_list)

        log.info(
            "PROGRESO  offset=%d  batch=%d  total=%d",
            offset, len(entities), total_processed,
        )

        if len(ids) < batch_size:
            break
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
        description="Builder de listing_market_facts."
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
