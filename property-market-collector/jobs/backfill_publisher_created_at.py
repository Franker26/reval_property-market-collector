#!/usr/bin/env python3
"""
jobs/backfill_publisher_created_at.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Backfill one-time de publisher_created_at desde extra_data.

Fase A: Parsea extra_data["publisher_created_date"] en listing_entities y guarda
        el valor en la nueva columna publisher_created_at.

Fase B: Propaga el valor a listing_snapshots via UPDATE JOIN (publisher_created_date
        es inmutable — el portal no modifica cuándo creó el listing).

Requisito: haber aplicado migrations/002_add_publisher_created_at.sql antes.

Uso:
    python jobs/backfill_publisher_created_at.py
    python jobs/backfill_publisher_created_at.py --batch-size 1000
    python jobs/backfill_publisher_created_at.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from datetime import datetime
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
log = logging.getLogger("jobs.backfill_publisher_created_at")

_DEFAULT_BATCH_SIZE = 1000


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    """Parsea ISO 8601 con offset estilo -0400 (sin colon separador)."""
    if not s:
        return None
    normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', s)
    try:
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


async def _backfill_entities(session, batch_size: int, dry_run: bool) -> int:
    """
    Fase A: lee listing_entities donde publisher_created_at IS NULL y
    extra_data tiene la clave 'publisher_created_date'.
    Actualiza publisher_created_at con el valor parseado.
    Retorna cantidad de filas actualizadas.
    """
    from sqlalchemy import select, text
    from app.db.models import ListingEntity

    offset = 0
    total_updated = 0

    while True:
        result = await session.execute(
            select(ListingEntity)
            .where(
                ListingEntity.publisher_created_at.is_(None),
                ListingEntity.extra_data.isnot(None),
                ListingEntity.extra_data["publisher_created_date"].isnot(None),
            )
            .order_by(ListingEntity.id)
            .limit(batch_size)
            .offset(offset)
        )
        entities = list(result.scalars().all())

        if not entities:
            break

        batch_updated = 0
        for entity in entities:
            raw_date = (entity.extra_data or {}).get("publisher_created_date")
            parsed = _parse_datetime(raw_date)
            if parsed is not None:
                if not dry_run:
                    entity.publisher_created_at = parsed
                batch_updated += 1

        if not dry_run:
            await session.flush()

        total_updated += batch_updated
        log.info(
            "  [entities] offset=%d  batch=%d  parsed=%d",
            offset, len(entities), batch_updated,
        )

        if len(entities) < batch_size:
            break
        offset += batch_size

    return total_updated


async def _backfill_snapshots(session, dry_run: bool) -> int:
    """
    Fase B: propaga publisher_created_at desde listing_entities a
    listing_snapshots via un UPDATE masivo.
    Retorna rowcount estimado.
    """
    from sqlalchemy import text

    stmt = text("""
        UPDATE listing_snapshots ls
        SET publisher_created_at = le.publisher_created_at
        FROM listing_entities le
        WHERE ls.listing_id = le.id
          AND ls.publisher_created_at IS NULL
          AND le.publisher_created_at IS NOT NULL
    """)

    if dry_run:
        count_stmt = text("""
            SELECT COUNT(*)
            FROM listing_snapshots ls
            JOIN listing_entities le ON ls.listing_id = le.id
            WHERE ls.publisher_created_at IS NULL
              AND le.publisher_created_at IS NOT NULL
        """)
        result = await session.execute(count_stmt)
        count = result.scalar_one()
        log.info("  [snapshots] dry-run — filas que se actualizarían: %d", count)
        return count
    else:
        result = await session.execute(stmt)
        return result.rowcount


async def run(batch_size: int, dry_run: bool) -> None:
    from app.db.session import get_async_session_factory

    factory = get_async_session_factory()
    t0 = time.monotonic()

    log.info("=" * 60)
    log.info("Backfill publisher_created_at — iniciando")
    log.info("  batch_size : %d", batch_size)
    log.info("  dry_run    : %s", dry_run)
    log.info("=" * 60)

    async with factory() as session:
        log.info("Fase A: actualizando listing_entities...")
        entities_updated = await _backfill_entities(session, batch_size, dry_run)
        log.info("  Fase A completada — %d entities actualizadas", entities_updated)

        log.info("Fase B: propagando a listing_snapshots...")
        snapshots_updated = await _backfill_snapshots(session, dry_run)
        log.info("  Fase B completada — %d snapshots actualizados", snapshots_updated)

        if not dry_run:
            await session.commit()
            log.info("Commit realizado.")
        else:
            log.info("Dry-run: ningún cambio persistido.")

    elapsed = time.monotonic() - t0
    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  listing_entities actualizadas  : %d", entities_updated)
    log.info("  listing_snapshots actualizados : %d", snapshots_updated)
    log.info("  Duración                       : %.1fs", elapsed)
    log.info("=" * 60)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill publisher_created_at desde extra_data."
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Listings por lote (default: {_DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Sin escritura a DB — solo loguear cantidades.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    asyncio.run(run(batch_size=args.batch_size, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
