#!/usr/bin/env python3
"""
jobs/zonaprop_discovery.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Job standalone: descubre todas las URLs de publicaciones de Zonaprop
a través de sus sitemaps públicos y escribe un inventario JSONL en:

    data/discovery/zonaprop_urls_YYYY-MM-DD.jsonl

Uso (desde la raíz del proyecto):
    python jobs/zonaprop_discovery.py

Cada línea del archivo de salida es un objeto JSON:
    {
      "source": "zonaprop",
      "url": "https://www.zonaprop.com.ar/propiedades/...html",
      "external_id": "58770807",
      "lastmod": "2026-05-25",
      "discovered_at": "2026-05-25T18:30:00-03:00",
      "discovery_source": "sitemap_prop_https_1.xml.gz"
    }

Este job NO llama a /extract y NO parsea páginas de detalle de propiedades.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

# ── Asegurar que la raíz del proyecto esté en sys.path ───────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from discovery.zonaprop_sitemap import discover_zonaprop_urls  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_discovery")

# ── Path de salida ────────────────────────────────────────────────────────────

_OUTPUT_DIR = _PROJECT_ROOT / "data" / "discovery"
_OUTPUT_FILENAME_TPL = "zonaprop_urls_{date}.jsonl"


def _output_path(today: date | None = None) -> Path:
    today = today or date.today()
    return _OUTPUT_DIR / _OUTPUT_FILENAME_TPL.format(date=today.isoformat())


# ── Escritura JSONL ───────────────────────────────────────────────────────────


def write_jsonl(records: list[dict], output_file: Path) -> None:
    """Escribe *records* en *output_file* como JSONL (un objeto JSON por línea)."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Entrypoint ────────────────────────────────────────────────────────────────


def main() -> int:
    log.info("=" * 60)
    log.info("Zonaprop sitemap discovery — iniciando")
    log.info("=" * 60)

    records, stats = discover_zonaprop_urls()

    output_file = _output_path()

    if records:
        write_jsonl(records, output_file)
        log.info("Archivo generado → %s", output_file)
    else:
        log.warning("No se descubrieron registros — archivo NO generado")

    # ── Resumen ───────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  sitemap_prop encontrados : %d", stats["sitemap_prop_found"])
    log.info("  sitemap_prop fallidos    : %d", stats["sitemap_prop_failed"])
    log.info("  URLs parseadas           : %d", stats["urls_parsed"])
    log.info("  URLs válidas             : %d", stats["urls_valid"])
    log.info("  Duplicados descartados   : %d", stats["duplicates_discarded"])
    if records:
        log.info("  Archivo generado         : %s", output_file)
    log.info("=" * 60)

    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
