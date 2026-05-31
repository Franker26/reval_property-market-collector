#!/usr/bin/env python3
"""
jobs/rehash_listings.py
~~~~~~~~~~~~~~~~~~~~~~~~
Recomputa content_hash de todos los listing_entities con el algoritmo actual.

Necesario cuando cambia _HASH_KEYS en app/core/hashing.py para evitar que
el próximo run de url_discovery trate todos los listings como modificados.

Solo actualiza listing_entities.content_hash — los snapshots históricos
no se tocan (registran el hash que tenían en el momento de captura).

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
from sqlalchemy.ext.asyncio import AsyncSession         # noqa: E402

from app.core.hashing import compute_listing_hash       # noqa: E402
from app.db.models import ListingEntity                 # noqa: E402
from app.db.session import get_async_session_factory    # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_PAYLOAD_KEYS = (
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


async def rehash(batch_size: int, dry_run: bool) -> None:
    factory = get_async_session_factory()
    total = changed = 0
    offset = 0

    log.info("Iniciando rehash%s (batch_size=%d)", " [DRY RUN]" if dry_run else "", batch_size)

    while True:
        async with factory() as session:
            result = await session.execute(
                select(ListingEntity)
                .order_by(ListingEntity.id)
                .limit(batch_size)
                .offset(offset)
            )
            batch = list(result.scalars().all())

        if not batch:
            break

        updates: list[dict] = []
        for entity in batch:
            posting = {k: getattr(entity, k, None) for k in _PAYLOAD_KEYS}
            new_hash = compute_listing_hash(posting)
            if new_hash != entity.content_hash:
                updates.append({"id": entity.id, "content_hash": new_hash})

        total += len(batch)
        changed += len(updates)

        if updates and not dry_run:
            async with factory() as session:
                async with session.begin():
                    for u in updates:
                        await session.execute(
                            update(ListingEntity)
                            .where(ListingEntity.id == u["id"])
                            .values(content_hash=u["content_hash"])
                        )

        log.info(
            "offset=%d  procesados=%d  actualizados=%d%s",
            offset, total, changed, " (DRY RUN)" if dry_run else "",
        )
        offset += batch_size

    log.info("Listo. Total: %d listings, %d hashes actualizados%s",
             total, changed, " (DRY RUN — sin cambios)" if dry_run else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rehashea listing_entities con el algoritmo actual.")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true", help="No escribe a la DB")
    args = parser.parse_args()
    asyncio.run(rehash(args.batch_size, args.dry_run))


if __name__ == "__main__":
    main()
