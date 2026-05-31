#!/usr/bin/env python3
"""
jobs/rehash_listings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Recomputa content_hash de listing_entities y listing_snapshots con el
algoritmo actual de hashing.

Necesario cuando cambia _HASH_KEYS o la lógica de normalización en
app/core/hashing.py, para evitar que el próximo run de url_discovery
trate todos los listings como modificados.

Uso:
    python jobs/rehash_listings.py
    python jobs/rehash_listings.py --dry-run
    python jobs/rehash_listings.py --batch-size 500
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select, update                   # noqa: E402

from app.core.hashing import compute_listing_hash       # noqa: E402
from app.db.models import ListingEntity, ListingSnapshot  # noqa: E402
from app.db.session import get_async_session_factory    # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_ENTITY_KEYS = (
    "canonical_url", "status", "source_modified_at",
    "operation_type", "property_type", "generated_title", "description",
    "price_amount", "price_currency", "expenses_amount", "expenses_currency",
    "surface_total", "surface_covered", "surface_unit",
    "rooms", "bedrooms", "bathrooms", "toilettes", "garages",
    "antiquity_years", "disposition", "orientation",
    "address", "neighborhood", "city", "province_name", "lat", "lon",
    "seller_id", "seller_name", "seller_type",
    "extra_data",
)

_SNAPSHOT_KEYS = (
    "canonical_url", "status", "source_modified_at",
    "operation_type", "property_type", "generated_title", "description",
    "price_amount", "price_currency", "expenses_amount", "expenses_currency",
    "surface_total", "surface_covered", "surface_unit",
    "rooms", "bedrooms", "bathrooms", "toilettes", "garages",
    "antiquity_years", "disposition", "orientation",
    "address", "neighborhood", "city", "province_name", "lat", "lon",
    "seller_id", "seller_name", "seller_type",
    "extra_data",
)


async def rehash_entities(batch_size: int, dry_run: bool) -> int:
    factory = get_async_session_factory()
    total = changed = 0
    offset = 0
    log.info("Rehashing listing_entities%s...", " [DRY RUN]" if dry_run else "")
    while True:
        async with factory() as session:
            result = await session.execute(
                select(ListingEntity).order_by(ListingEntity.id)
                .limit(batch_size).offset(offset)
            )
            batch = list(result.scalars().all())
        if not batch:
            break
        updates = []
        for entity in batch:
            data = {k: getattr(entity, k, None) for k in _ENTITY_KEYS}
            new_hash = compute_listing_hash(data)
            if new_hash != entity.content_hash:
                updates.append({"id": entity.id, "content_hash": new_hash})
        if updates and not dry_run:
            async with factory() as session:
                async with session.begin():
                    for u in updates:
                        await session.execute(
                            update(ListingEntity)
                            .where(ListingEntity.id == u["id"])
                            .values(content_hash=u["content_hash"])
                        )
        total += len(batch)
        changed += len(updates)
        offset += batch_size
    log.info("listing_entities: %d procesados, %d actualizados%s",
             total, changed, " (DRY RUN)" if dry_run else "")
    return changed


async def rehash_snapshots(batch_size: int, dry_run: bool) -> int:
    factory = get_async_session_factory()
    total = changed = 0
    offset = 0
    log.info("Rehashing listing_snapshots%s...", " [DRY RUN]" if dry_run else "")
    while True:
        async with factory() as session:
            result = await session.execute(
                select(ListingSnapshot).order_by(ListingSnapshot.id)
                .limit(batch_size).offset(offset)
            )
            batch = list(result.scalars().all())
        if not batch:
            break
        updates = []
        for snap in batch:
            data = {k: getattr(snap, k, None) for k in _SNAPSHOT_KEYS}
            new_hash = compute_listing_hash(data)
            if new_hash != snap.content_hash:
                updates.append({"id": snap.id, "content_hash": new_hash})
        if updates and not dry_run:
            async with factory() as session:
                async with session.begin():
                    for u in updates:
                        await session.execute(
                            update(ListingSnapshot)
                            .where(ListingSnapshot.id == u["id"])
                            .values(content_hash=u["content_hash"])
                        )
        total += len(batch)
        changed += len(updates)
        offset += batch_size
    log.info("listing_snapshots: %d procesados, %d actualizados%s",
             total, changed, " (DRY RUN)" if dry_run else "")
    return changed


async def run(batch_size: int, dry_run: bool) -> None:
    await rehash_entities(batch_size, dry_run)
    await rehash_snapshots(batch_size, dry_run)
    log.info("Listo%s.", " (DRY RUN — sin cambios)" if dry_run else "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.batch_size, args.dry_run))


if __name__ == "__main__":
    main()
