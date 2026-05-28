"""
discovery.zonaprop.listing_pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Crawl asíncrono de páginas de listado paginadas de Zonaprop.

Usa httpx como método principal. Para páginas ≥10, Cloudflare bloquea
requests sin JavaScript: en esos casos se usa un fallback_fetch callable
(normalmente sources.browser.fetch_rendered con Playwright) que se pasa
opcionalmente desde el job.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, Optional

import httpx

from .models import DiscoveryRecord
from .parser import parse_listing_page

log = logging.getLogger(__name__)

_ART = timezone(timedelta(hours=-3))

_HTTPX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
    # Sin 'br': no hay librería brotli instalada; el servidor respondería con
    # Content-Encoding: br y httpx no podría descomprimir el cuerpo.
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_PAGINA_RE = re.compile(r"-pagina-\d+\.html$")

_STATUS_OK    = "ok"
_STATUS_EMPTY = "empty"
_STATUS_ERROR = "error"


# ── URL builder ───────────────────────────────────────────────────────────────


def build_listing_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    clean = _PAGINA_RE.sub(".html", base_url)
    return clean.rstrip("/").replace(".html", f"-pagina-{page}.html")


# ── Fetch con retry y fallback Playwright ─────────────────────────────────────


async def fetch_listing_page(
    url: str,
    client: httpx.AsyncClient,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
) -> str | None:
    """
    Descarga el HTML de una página de listado.

    1. Intenta con httpx (sin retry en 403: Cloudflare bloquea permanentemente
       páginas ≥10 para no-browsers, más retries solo agregan latencia).
    2. Si recibe 403 y se pasó `fallback_fetch` (Playwright), lo usa.
    3. Retorna None si todo falla.
    """
    try:
        resp = await client.get(url)
        if resp.status_code == 403:
            if fallback_fetch is not None:
                log.info("listing_pages: 403 en %s — usando fallback (Playwright)", url)
                try:
                    return await fallback_fetch(url)
                except Exception as exc:
                    log.warning("listing_pages: fallback falló en %s — %s", url, exc)
                    return None
            log.warning("listing_pages: 403 en %s (sin fallback configurado)", url)
            return None
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as exc:
        log.warning("listing_pages: HTTP %d en %s", exc.response.status_code, url)
        return None
    except Exception as exc:
        log.warning("listing_pages: error al descargar %s — %s", url, exc)
        return None


# ── Página individual ─────────────────────────────────────────────────────────


async def discover_listing_page(
    base_url: str,
    page: int,
    operation_type: str,
    client: httpx.AsyncClient,
    discovered_at: str,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
) -> tuple[list[DiscoveryRecord], str]:
    url = build_listing_url(base_url, page)
    html = await fetch_listing_page(url, client, fallback_fetch=fallback_fetch)
    if html is None:
        return [], _STATUS_ERROR
    records = parse_listing_page(
        html=html,
        search_url=base_url,
        page=page,
        operation_type=operation_type,
        discovered_at=discovered_at,
    )
    status = _STATUS_OK if records else _STATUS_EMPTY
    return records, status


# ── Catálogo completo ─────────────────────────────────────────────────────────


async def discover_catalog(
    base_url: str,
    operation_type: str,
    start_page: int,
    max_pages: int,
    concurrency: int,
    delay: float,
    on_page_done: Optional[Callable[[int, list[DiscoveryRecord], bool], Awaitable[None]]] = None,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
) -> tuple[list[DiscoveryRecord], dict]:
    """
    Crawlea páginas de listado de forma concurrente y devuelve (records, stats).

    fallback_fetch: callable async(url) → html para usar cuando httpx recibe 403.
    Normalmente se pasa sources.browser.fetch_rendered desde el job.
    """
    discovered_at = datetime.now(_ART).isoformat(timespec="seconds")
    sem = asyncio.Semaphore(concurrency)
    all_records: list[DiscoveryRecord] = []

    stats: dict = {
        "pages_processed": 0,
        "pages_ok": 0,
        "pages_failed": 0,
        "urls_total": 0,
    }

    consecutive_empty = 0
    _EMPTY_THRESHOLD = 3

    async def _fetch_one(page: int) -> tuple[int, list[DiscoveryRecord], str]:
        async with sem:
            if delay > 0:
                await asyncio.sleep(delay)
            records, status = await discover_listing_page(
                base_url, page, operation_type, client, discovered_at,
                fallback_fetch=fallback_fetch,
            )
            return page, records, status

    async with httpx.AsyncClient(
        headers=_HTTPX_HEADERS,
        follow_redirects=True,
        timeout=30,
    ) as client:
        pages = range(start_page, start_page + max_pages)
        tasks = [asyncio.create_task(_fetch_one(p)) for p in pages]

        for coro in asyncio.as_completed(tasks):
            try:
                page, records, status = await coro
            except asyncio.CancelledError:
                continue

            stats["pages_processed"] += 1

            if status == _STATUS_OK:
                stats["pages_ok"] += 1
                stats["urls_total"] += len(records)
                all_records.extend(records)
                consecutive_empty = 0

            elif status == _STATUS_EMPTY:
                stats["pages_failed"] += 1
                consecutive_empty += 1
                log.debug("listing_pages: página %d vacía (consecutive: %d)", page, consecutive_empty)

            else:  # _STATUS_ERROR
                stats["pages_failed"] += 1
                log.warning("listing_pages: error en página %d — continuando", page)

            if on_page_done is not None:
                await on_page_done(page, records, status == _STATUS_OK)

            if consecutive_empty >= _EMPTY_THRESHOLD:
                log.info(
                    "listing_pages: %d páginas vacías (200) consecutivas — fin de catálogo",
                    consecutive_empty,
                )
                for t in tasks:
                    t.cancel()
                break

    return all_records, stats
