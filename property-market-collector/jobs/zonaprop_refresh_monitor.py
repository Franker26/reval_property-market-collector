#!/usr/bin/env python3
"""
jobs/zonaprop_refresh_monitor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Refresh rotativo priorizado de segmentos Zonaprop (Etapa A).

Reencola en la scan_queue las hojas activas 'complete' vencidas según su tier
(hot/warm/cold), priorizando volatilidad histórica + volumen. No escanea: deja los
segmentos en 'pending' para que url_discovery los reprocese y detecte cambios
individuales vía upsert_batch.

Uso:
    python jobs/zonaprop_refresh_monitor.py
    python jobs/zonaprop_refresh_monitor.py --portal zonaprop
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_refresh_monitor")

_PORTAL = "zonaprop"
_SOURCE_CODE = "zonaprop"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh monitor de segmentos Zonaprop.")
    p.add_argument("--portal", default=_PORTAL)
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from app.services.discovery_service import run_refresh_monitor

    result = await run_refresh_monitor(portal=args.portal, source_code=_SOURCE_CODE, mode="manual")
    log.info("refresh_monitor finalizado — %s", result)
    return 0 if result.get("status") == "success" else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
