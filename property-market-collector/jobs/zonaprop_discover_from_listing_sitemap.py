#!/usr/bin/env python3
"""
jobs/zonaprop_discover_from_listing_sitemap.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Discovery full-catalog de Zonaprop usando las 156k URLs de búsqueda
filtrada de los sitemaps de listing pages (sitemap_list_https_*.xml).

Para cada URL del sitemap, extrae las páginas 1-9 con httpx (zona
segura de Cloudflare) y acumula los resultados en un JSONL deduplicado.

Salida:
    data/discovery/zonaprop_from_listing_sitemaps_YYYY-MM-DD.jsonl

Uso rápido (con archivos locales, 200 URLs para test):
    python jobs/zonaprop_discover_from_listing_sitemap.py \\
        --local-sitemaps \\
            /home/.../sitemap_list_https_1.xml \\
            /home/.../sitemap_list_https_2.xml \\
            /home/.../sitemap_list_https_3.xml \\
            /home/.../sitemap_list_https_4.xml \\
        --limit 200 --max-pages-per-url 9 --delay 1.0

Run completo (producción, con resume):
    python jobs/zonaprop_discover_from_listing_sitemap.py \\
        --max-pages-per-url 9 --concurrency 3 --delay 1.0 --resume
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

from discovery.zonaprop.listing_sitemap import (  # noqa: E402
    iter_all_listing_urls,
    infer_operation_type,
)
from discovery.zonaprop.listing_pages import discover_catalog  # noqa: E402
from discovery.zonaprop.models import DiscoveryRecord           # noqa: E402

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
log = logging.getLogger("jobs.zonaprop_discover_from_listing_sitemap")

# ── Constantes ────────────────────────────────────────────────────────────────

_ART = timezone(timedelta(hours=-3))
_DISCOVERY_DIR = _PROJECT_ROOT / "data" / "discovery"
_OUTPUT_TPL    = "zonaprop_from_listing_sitemaps_{date}.jsonl"
_STATE_FILE    = _DISCOVERY_DIR / "zonaprop_listing_sitemaps_state.json"
_STATE_SAVE_EVERY = 50   # guardar state cada N listing URLs procesadas


# ── Path helper ───────────────────────────────────────────────────────────────


def _default_output(today: date | None = None) -> Path:
    today = today or date.today()
    return _DISCOVERY_DIR / _OUTPUT_TPL.format(date=today.isoformat())


# ── State (resume de listing URLs) ────────────────────────────────────────────


def _load_state(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        processed = set(data.get("processed_urls", []))
        log.info("resume: %d listing URLs ya procesadas (state)", len(processed))
        return processed
    except Exception as exc:
        log.warning("state: no se pudo cargar %s — %s", state_file, exc)
        return set()


def _save_state(
    state_file: Path,
    processed_urls: set[str],
    total_written: int,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "processed_urls": sorted(processed_urls),
        "last_updated": datetime.now(_ART).isoformat(timespec="seconds"),
        "total_processed": len(processed_urls),
        "total_written": total_written,
    }
    state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Resume de external_ids ────────────────────────────────────────────────────


def _load_seen_ids(output_file: Path) -> set[str]:
    seen: set[str] = set()
    if not output_file.exists():
        return seen
    with output_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ext_id = json.loads(line).get("external_id")
                if ext_id:
                    seen.add(ext_id)
            except (json.JSONDecodeError, AttributeError):
                pass
    log.info("resume: %d external_ids ya presentes en el output", len(seen))
    return seen


# ── Escritura JSONL ───────────────────────────────────────────────────────────


class _Writer:
    def __init__(self, output_file: Path, seen_ids: set[str]) -> None:
        self._file = output_file
        self._seen = seen_ids
        self._lock = asyncio.Lock()
        self._written = 0
        self._skipped = 0
        output_file.parent.mkdir(parents=True, exist_ok=True)

    async def write_records(self, records: list[DiscoveryRecord]) -> tuple[int, int]:
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


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Discovery full-catalog de Zonaprop desde listing sitemaps."
    )
    p.add_argument(
        "--local-sitemaps",
        nargs="+",
        metavar="PATH",
        default=None,
        help="Paths locales a los XML de listing sitemaps (dev/test)",
    )
    p.add_argument("--max-pages-per-url", type=int, default=5,
                   help="Máximo de páginas por listing URL (robots.txt permite 2-5; 6-9 grey zone)")
    p.add_argument("--concurrency", type=int, default=3,
                   help="Páginas concurrentes dentro de cada URL")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Segundos entre listing URLs (evitar rate limiting)")
    p.add_argument("--output", default=None, help="Path del JSONL de salida")
    p.add_argument("--state", default=str(_STATE_FILE), help="Path del state file")
    p.add_argument("--resume", action="store_true",
                   help="Saltar listing URLs ya procesadas y external_ids ya escritos")
    p.add_argument("--progress-every", type=int, default=100,
                   help="Log cada N listing URLs procesadas")
    p.add_argument("--limit", type=int, default=None,
                   help="Limitar total de listing URLs (para tests)")
    return p.parse_args()


# ── Core async ────────────────────────────────────────────────────────────────


async def _run(args: argparse.Namespace) -> int:
    output_file = Path(args.output) if args.output else _default_output()
    state_file  = Path(args.state)

    # Estado y dedup
    processed_urls: set[str] = set()
    seen_ids: set[str] = set()
    if args.resume:
        processed_urls = _load_state(state_file)
        seen_ids = _load_seen_ids(output_file)

    writer = _Writer(output_file, seen_ids)

    # Playwright como fallback silencioso
    playwright_ok = False
    try:
        await _browser.get_browser()
        playwright_ok = True
    except Exception as exc:
        log.warning("Playwright no disponible: %s — continuando sin fallback", exc)

    log.info("=" * 60)
    log.info("Zonaprop listing sitemaps discovery — iniciando")
    log.info("  max_pages_per_url : %d", args.max_pages_per_url)
    log.info("  concurrency       : %d", args.concurrency)
    log.info("  delay entre URLs  : %.1fs", args.delay)
    log.info("  output            : %s", output_file)
    log.info("  resume            : %s", args.resume)
    log.info("  playwright        : %s", "activo" if playwright_ok else "inactivo")
    log.info("=" * 60)

    start_time = time.monotonic()
    total_listing_urls = 0
    skipped_urls = 0
    processed_count = 0
    failed_urls = 0
    total_raw = 0

    fallback = _browser.fetch_rendered if playwright_ok else None

    try:
        # Iterar las listing URLs del sitemap
        for listing_url in iter_all_listing_urls(local_paths=args.local_paths):
            total_listing_urls += 1

            if args.limit and total_listing_urls > args.limit:
                log.info("--limit %d alcanzado, deteniendo", args.limit)
                break

            # Resume: saltar URLs ya procesadas
            if listing_url in processed_urls:
                skipped_urls += 1
                continue

            # Delay entre URLs
            if processed_count > 0 and args.delay > 0:
                await asyncio.sleep(args.delay)

            operation_type = infer_operation_type(listing_url)

            # Callback: recibe los resultados de cada página
            page_records_buffer: list[DiscoveryRecord] = []

            async def on_page_done(
                page: int,
                records: list[DiscoveryRecord],
                success: bool,
                _buf: list = page_records_buffer,
            ) -> None:
                _buf.extend(records)

            try:
                _, crawl_stats = await discover_catalog(
                    base_url=listing_url,
                    operation_type=operation_type,
                    start_page=1,
                    max_pages=args.max_pages_per_url,
                    concurrency=args.concurrency,
                    delay=0.0,
                    on_page_done=on_page_done,
                    fallback_fetch=fallback,
                )
                total_raw += crawl_stats.get("urls_total", 0)
                written, _ = await writer.write_records(page_records_buffer)
            except Exception as exc:
                log.warning("error procesando %s — %s", listing_url, exc)
                failed_urls += 1
            else:
                processed_urls.add(listing_url)
                processed_count += 1

            # Progress log
            done = processed_count + skipped_urls
            if done % args.progress_every == 0 and done > 0:
                elapsed = time.monotonic() - start_time
                rate = done / (elapsed / 60) if elapsed > 0 else 0
                log.info(
                    "progreso: %d procesadas (%d saltadas) | %d escritas | %.1f URLs/min",
                    processed_count, skipped_urls, writer.total_written, rate,
                )

            # Guardar state periódicamente
            if processed_count % _STATE_SAVE_EVERY == 0 and processed_count > 0:
                _save_state(state_file, processed_urls, writer.total_written)

    finally:
        # Guardar state final siempre
        _save_state(state_file, processed_urls, writer.total_written)
        await _browser.close()

    elapsed = time.monotonic() - start_time
    elapsed_str = f"{int(elapsed // 3600)}h {int((elapsed % 3600) // 60)}m {int(elapsed % 60)}s"
    rate = (processed_count + skipped_urls) / (elapsed / 60) if elapsed > 0 else 0

    log.info("=" * 60)
    log.info("RESUMEN")
    log.info("  listing URLs vistas      : %d", total_listing_urls)
    log.info("  listing URLs procesadas  : %d", processed_count)
    log.info("  listing URLs saltadas    : %d (resume)", skipped_urls)
    log.info("  listing URLs con error   : %d", failed_urls)
    log.info("  URLs brutas encontradas  : %d", total_raw)
    log.info("  URLs únicas escritas     : %d", writer.total_written)
    log.info("  Duplicados descartados   : %d", writer.total_skipped)
    log.info("  duración                 : %s", elapsed_str)
    log.info("  velocidad                : %.1f listing URLs/min", rate)
    log.info("  output                   : %s", output_file)
    log.info("  state                    : %s", state_file)
    log.info("=" * 60)

    return 0 if writer.total_written > 0 else 1


def main() -> int:
    args = _parse_args()
    # Mapear --local-sitemaps a args.local_paths (nombre interno)
    args.local_paths = args.local_sitemaps
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
