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


def _compute_location(entity) -> dict:
    """Computa el dict de facts de ubicación para un listing."""
    has_coords = entity.lat is not None and entity.lon is not None

    if has_coords:
        return {
            "listing_id":              entity.id,
            "raw_province":            entity.province_name,
            "raw_city":                entity.city,
            "raw_neighborhood":        entity.neighborhood,
            "raw_address":             entity.address,
            "raw_latitude":            entity.lat,
            "raw_longitude":           entity.lon,
            "normalized_country":      "AR",
            "normalized_province":     entity.province_name,
            "normalized_city":         entity.city,
            "normalized_neighborhood": entity.neighborhood,
            "normalized_address":      entity.address,
            "normalized_latitude":     entity.lat,
            "normalized_longitude":    entity.lon,
            "geo_provider":            "portal",
            "geo_provider_place_id":   None,
            "geo_confidence":          "high",
            "geo_status":              "coordinates",
            "geo_error":               None,
        }
    else:
        return {
            "listing_id":              entity.id,
            "raw_province":            entity.province_name,
            "raw_city":                entity.city,
            "raw_neighborhood":        entity.neighborhood,
            "raw_address":             entity.address,
            "raw_latitude":            None,
            "raw_longitude":           None,
            "normalized_country":      None,
            "normalized_province":     entity.province_name,
            "normalized_city":         entity.city,
            "normalized_neighborhood": entity.neighborhood,
            "normalized_address":      None,
            "normalized_latitude":     None,
            "normalized_longitude":    None,
            "geo_provider":            None,
            "geo_provider_place_id":   None,
            "geo_confidence":          None,
            "geo_status":              "pending",
            "geo_error":               None,
        }


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


_LLN_UPDATE_KEYS = frozenset({
    "raw_province", "raw_city", "raw_neighborhood", "raw_address",
    "raw_latitude", "raw_longitude",
    "normalized_country", "normalized_province", "normalized_city",
    "normalized_neighborhood", "normalized_address",
    "normalized_latitude", "normalized_longitude",
    "geo_provider", "geo_provider_place_id", "geo_confidence",
    "geo_status", "geo_error",
})


async def _upsert_batch(session, location_list: list[dict]) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.db.models.location_normalization import ListingLocationNormalization

    if not location_list:
        return

    stmt = pg_insert(ListingLocationNormalization).values(location_list)
    stmt = stmt.on_conflict_do_update(
        index_elements=["listing_id"],
        set_={k: stmt.excluded[k] for k in _LLN_UPDATE_KEYS if k in location_list[0]},
    )
    await session.execute(stmt)


async def run(mode: str, batch_size: int, source_id: Optional[int], dry_run: bool) -> None:
    from sqlalchemy import select
    from app.db.session import get_async_session_factory
    from app.db.models import ListingEntity, Base
    from app.db.models.location_normalization import ListingLocationNormalization

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

        location_list = [_compute_location(e) for e in entities]
        total_processed += len(entities)

        if not dry_run:
            async with factory() as session:
                await _upsert_batch(session, location_list)
                await session.commit()
            total_upserted += len(location_list)

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
