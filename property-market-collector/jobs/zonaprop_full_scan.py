#!/usr/bin/env python3
"""
jobs/zonaprop_full_scan.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Full scan de salida en vivo (Etapa B): construye el baseline de churn del parque.

Dos ciclos sobre la infraestructura existente (scan_queue -> url_discovery ->
upsert_batch -> snapshots), sin pipeline paralelo:

  baseline  reencola todas las hojas activas refreshables con priority
            'full_scan_baseline'. Pasa por mark_complete (deja completed_at como
            referencia temporal) pero NO alimenta churn_ewma.
  compare   segundo ciclo con priority 'full_scan_compare': usa el completed_at
            del baseline para calcular el primer churn diario válido del parque.

Doble gate anti-accidente: requiere FULL_SCAN_ENABLED=true Y un batch_id
explícito (--batch-id o FULL_SCAN_BATCH_ID). Idempotente y reanudable: el estado
durable del batch vive en zonaprop_segment_scan_history, así que se puede correr
repetidamente (respeta FULL_SCAN_MAX_PAGES_PER_CYCLE por ejecución) hasta que
'remaining' llegue a 0; recién entonces pasar a compare.

Uso:
    python jobs/zonaprop_full_scan.py --mode baseline --batch-id golive-2026-06
    python jobs/zonaprop_full_scan.py --mode compare  --batch-id golive-2026-06
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_full_scan")

_PORTAL = "zonaprop"
_SOURCE_CODE = "zonaprop"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full scan baseline/compare de Zonaprop (salida en vivo).")
    p.add_argument("--mode", required=True, choices=["baseline", "compare"])
    p.add_argument(
        "--batch-id",
        default=os.getenv("FULL_SCAN_BATCH_ID", ""),
        help="Identificador rastreable del batch (obligatorio; fallback: FULL_SCAN_BATCH_ID)",
    )
    p.add_argument("--portal", default=_PORTAL)
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from app.core.config import get_full_scan_config
    from app.services.discovery_service import run_full_scan

    if not get_full_scan_config().enabled:
        log.error("full_scan deshabilitado: setear FULL_SCAN_ENABLED=true para la salida en vivo")
        return 1
    if not args.batch_id:
        log.error("batch_id requerido: pasar --batch-id o setear FULL_SCAN_BATCH_ID")
        return 1

    result = await run_full_scan(
        scan_mode=args.mode,
        batch_id=args.batch_id,
        portal=args.portal,
        source_code=_SOURCE_CODE,
        mode="manual",
    )
    log.info("full_scan finalizado — %s", result)
    if result.get("status") != "success":
        return 1
    remaining = result.get("remaining", 0)
    if remaining:
        log.info(
            "full_scan: quedan %d segmentos fuera de este ciclo — re-ejecutar el job "
            "hasta remaining=0 antes de pasar al siguiente modo", remaining,
        )
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
