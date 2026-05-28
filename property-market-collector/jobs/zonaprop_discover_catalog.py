#!/usr/bin/env python3
"""
jobs/zonaprop_discover_catalog.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Job standalone: descubre el catálogo completo de Zonaprop crawleando
páginas de listado paginadas (no sitemaps) y escribe un JSONL incremental.

Salida por defecto:
    data/discovery/zonaprop_catalog_YYYY-MM-DD.jsonl

Uso:
    python jobs/zonaprop_discover_catalog.py \\
        --base-url https://www.zonaprop.com.ar/inmuebles-venta.html \\
        --start-page 1 \\
        --max-pages 100 \\
        --concurrency 5

Con resume (no reescribe external_ids ya presentes en el archivo):
    python jobs/zonaprop_discover_catalog.py --resume
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# ── sys.path ──────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from discovery.zonaprop.listing_pages import discover_catalog  # noqa: E402
from discovery.zonaprop.models import DiscoveryRecord           # noqa: E402

# Importar browser singleton (Playwright) para páginas que Cloudflare bloquea a httpx.
# Se usa solo como fallback cuando httpx recibe 403.
import types as _types
if "sources" not in sys.modules:
    _stub = _types.ModuleType("sources")
    _stub.__path__ = [str(_PROJECT_ROOT / "sources")]  # type: ignore[attr-defined]
    _stub.__package__ = "sources"
    sys.modules["sources"] = _stub
from sources import browser as _browser  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("jobs.zonaprop_discover_catalog")

# ── Constantes ────────────────────────────────────────────────────────────────

_ART = timezone(timedelta(hours=-3))
_DISCOVERY_DIR = _PROJECT_ROOT / "data" / "discovery"
_OUTPUT_TPL = "zonaprop_catalog_{date}.jsonl"

_DEFAULT_BASE_URL = "https://www.zonaprop.com.ar/inmuebles-venta.html"


# ── Path helpers ──────────────────────────────────────────────────────────────


def _default_output(today: date | None = None) -> Path:
    today = today or date.today()
    return _DISCOVERY_DIR / _OUTPUT_TPL.format(date=today.isoformat())


# ── Resume ────────────────────────────────────────────────────────────────────


def _load_seen_ids(output_file: Path) -> set[str]:
    """Carga los external_ids ya presentes en el archivo de salida."""
    seen: set[str] = set()
    if not output_file.exists():
        return seen
    with output_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ext_id = rec.get("external_id")
                if ext_id:
                    seen.add(ext_id)
            except (json.JSONDecodeError, AttributeError):
                pass
    log.info("resume: %d external_ids ya presentes en el archivo", len(seen))
    return seen


# ── Escritura JSONL ───────────────────────────────────────────────────────────


class _Writer:
    """Escritura incremental thread-safe para asyncio."""

    def __init__(self, output_file: Path, seen_ids: set[str]) -> None:
        self._file = output_file
        self._seen = seen_ids
        self._lock = asyncio.Lock()
        self._written = 0
        self._skipped = 0
        output_file.parent.mkdir(parents=True, exist_ok=True)

    async def write_records(self, records: list[DiscoveryRecord]) -> tuple[int, int]:
        """Escribe los registros nuevos; retorna (escritos, saltados)."""
        new_records = [r for r in records if r.external_id not in self._seen]
        skipped = len(records) - len(new_records)

        if new_records:
            async with self._lock:
                with self._file.open("a", encoding="utf-8") as fh:
                    for rec in new_records:
                        fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
                for rec in new_records:
                    self._seen.add(rec.external_id)
                self._written += len(new_records)

        self._skipped += skipped
        return len(new_records), skipped

    @property
    def total_written(self) -> int:
        return self._written

    @property
    def total_skipped(self) -> int:
        return self._skipped


# ── Entrypoint ────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Descubre el catálogo completo de Zonaprop desde páginas de listado."
    )
    p.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    p.add_argument("--operation-type", default="venta")
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--output", default=None, help="Path del JSONL de salida")
    p.add_argument("--resume", action="store_true", help="No reescribir IDs ya presentes")
    p.add_argument("--progress-every", type=int, default=50)
    p.add_argument("--delay", type=float, default=0.0, help="Segundos entre requests por worker")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    output_file = Path(args.output) if args.output else _default_output()

    seen_ids: set[str] = set()
    if args.resume:
        seen_ids = _load_seen_ids(output_file)

    writer = _Writer(output_file, seen_ids)

    pages_reported: list[int] = []
    start_time = time.monotonic()

    async def on_page_done(page: int, records: list[DiscoveryRecord], success: bool) -> None:
        written, skipped = await writer.write_records(records)
        pages_reported.append(page)

        n = len(pages_reported)
        if n % args.progress_every == 0:
            elapsed = time.monotonic() - start_time
            rate = n / (elapsed / 60) if elapsed > 0 else 0
            log.info(
                "progreso: %d páginas procesadas | %d URLs escritas | %.1f páginas/min",
                n,
                writer.total_written,
                rate,
            )

        if not success:
            log.warning("página %d: sin resultados (error o vacía)", page)

    # Inicializar Playwright (fallback para páginas que Cloudflare bloquea a httpx)
    log.info("iniciando browser (Playwright) para fallback en 403...")
    try:
        await _browser.get_browser()
        playwright_ok = True
    except Exception as exc:
        log.warning("no se pudo inicializar Playwright: %s — continuando sin fallback", exc)
        playwright_ok = False

    log.info("=" * 60)
    log.info("Zonaprop catalog discovery — iniciando")
    log.info("  base_url       : %s", args.base_url)
    log.info("  start_page     : %d", args.start_page)
    log.info("  max_pages      : %d", args.max_pages)
    log.info("  concurrency    : %d", args.concurrency)
    log.info("  delay          : %.2fs", args.delay)
    log.info("  output         : %s", output_file)
    log.info("  resume         : %s", args.resume)
    log.info("  playwright     : %s", "activo" if playwright_ok else "inactivo")
    log.info("=" * 60)

    try:
        all_records, crawl_stats = await discover_catalog(
            base_url=args.base_url,
            operation_type=args.operation_type,
            start_page=args.start_page,
            max_pages=args.max_pages,
            concurrency=args.concurrency,
            delay=args.delay,
            on_page_done=on_page_done,
            fallback_fetch=_browser.fetch_rendered if playwright_ok else None,
        )
    finally:
        await _browser.close()

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    rate = crawl_stats["pages_processed"] / (elapsed / 60) if elapsed > 0 else 0

    # Contar duplicados globales (entre páginas distintas)
    global_dupes = crawl_stats["urls_total"] - writer.total_written - writer.total_skipped

    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  base_url            : %s", args.base_url)
    log.info("  start_page          : %d", args.start_page)
    log.info("  max_pages           : %d", args.max_pages)
    log.info("  páginas procesadas  : %d", crawl_stats["pages_processed"])
    log.info("  páginas exitosas    : %d", crawl_stats["pages_ok"])
    log.info("  páginas fallidas    : %d", crawl_stats["pages_failed"])
    log.info("  URLs descubiertas   : %d", crawl_stats["urls_total"])
    log.info("  URLs escritas       : %d", writer.total_written)
    log.info("  Saltadas (resume)   : %d", writer.total_skipped)
    log.info("  Duplicados (inter)  : %d", max(0, global_dupes))
    log.info("  duración            : %s", elapsed_str)
    log.info("  velocidad           : %.1f páginas/min", rate)
    log.info("  archivo             : %s", output_file)
    log.info("=" * 60)

    return 0 if writer.total_written > 0 else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
