"""
discovery.zonaprop.listing_pages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Crawl de páginas de listado paginadas de Zonaprop.

Diseñado para parecer tráfico humano:
- Sesión httpx persistente (cookies + keep-alive reutilizados entre requests)
- Delays aleatorios con jitter natural
- Referer dinámico que simula navegar de página en página
- Sin paralelismo agresivo: una página a la vez por URL
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, Optional

import httpx

from .models import DiscoveryRecord
from .parser import parse_listing_page

log = logging.getLogger(__name__)

_ART = timezone(timedelta(hours=-3))

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept-Encoding": "gzip, deflate",
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


# ── Fetch humanizado ──────────────────────────────────────────────────────────


def _jitter(base: float) -> float:
    """Delay con jitter: base ± 40%, mínimo 0.3s."""
    return max(0.3, base * random.uniform(0.6, 1.4))


async def fetch_listing_page(
    url: str,
    client: httpx.AsyncClient,
    referer: str | None = None,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
) -> str | None:
    """
    Descarga el HTML de una página de listado.
    Agrega Referer cuando corresponde (simula navegar de página en página).
    Fallback a Playwright si recibe 403.
    """
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Dest"] = "document"
    else:
        headers["Sec-Fetch-Site"] = "none"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Dest"] = "document"

    try:
        resp = await client.get(url, headers=headers)
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
    referer: str | None = None,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
) -> tuple[list[DiscoveryRecord], str]:
    url = build_listing_url(base_url, page)
    html = await fetch_listing_page(url, client, referer=referer, fallback_fetch=fallback_fetch)
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


# ── Catálogo por URL — secuencial con Referer encadenado ──────────────────────


async def discover_catalog(
    base_url: str,
    operation_type: str,
    start_page: int,
    max_pages: int,
    concurrency: int,
    delay: float,
    on_page_done: Optional[Callable[[int, list[DiscoveryRecord], bool], Awaitable[None]]] = None,
    fallback_fetch: Optional[Callable[[str], Awaitable[str]]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[list[DiscoveryRecord], dict]:
    """
    Crawlea páginas 1-N de una listing URL de forma secuencial.

    Parámetros de humanización:
        delay   — tiempo base en segundos entre páginas (se aplica jitter ±40%)
        client  — cliente httpx externo (sesión persistente con cookies).
                  Si es None se crea uno local (solo para esa URL).

    El Referer se encadena: página 2 referencia a página 1, etc.
    """
    discovered_at = datetime.now(_ART).isoformat(timespec="seconds")
    all_records: list[DiscoveryRecord] = []

    stats: dict = {
        "pages_processed": 0,
        "pages_ok": 0,
        "pages_failed": 0,
        "urls_total": 0,
    }

    consecutive_empty = 0
    _EMPTY_THRESHOLD = 3

    async def _run(c: httpx.AsyncClient) -> None:
        nonlocal consecutive_empty
        prev_url: str | None = None

        for page in range(start_page, start_page + max_pages):
            if page > start_page and delay > 0:
                await asyncio.sleep(_jitter(delay))

            records, status = await discover_listing_page(
                base_url, page, operation_type, c, discovered_at,
                referer=prev_url,
                fallback_fetch=fallback_fetch,
            )

            stats["pages_processed"] += 1

            if status == _STATUS_OK:
                stats["pages_ok"] += 1
                stats["urls_total"] += len(records)
                all_records.extend(records)
                consecutive_empty = 0
                prev_url = build_listing_url(base_url, page)

            elif status == _STATUS_EMPTY:
                stats["pages_failed"] += 1
                consecutive_empty += 1
                log.debug("listing_pages: página %d vacía (%d consecutivas)", page, consecutive_empty)

            else:
                stats["pages_failed"] += 1
                log.warning("listing_pages: error en página %d — continuando", page)

            if on_page_done is not None:
                await on_page_done(page, records, status == _STATUS_OK)

            if consecutive_empty >= _EMPTY_THRESHOLD:
                log.debug(
                    "listing_pages: %d páginas vacías consecutivas — fin de catálogo para %s",
                    consecutive_empty, base_url,
                )
                break

    if client is not None:
        await _run(client)
    else:
        async with httpx.AsyncClient(
            headers=_BASE_HEADERS,
            follow_redirects=True,
            timeout=30,
        ) as local_client:
            await _run(local_client)

    return all_records, stats


# ── Crear cliente de sesión persistente ───────────────────────────────────────


def make_session_client(proxy: str | None = None) -> httpx.AsyncClient:
    """
    Crea un AsyncClient de larga vida para compartir entre múltiples llamadas
    a discover_catalog. Mantiene cookies y conexiones keep-alive, simulando
    una sesión de navegador real.

    proxy: URL del proxy (ej: "http://user:pass@host:port"). Si es None,
           usa la conexión directa.
    """
    kwargs: dict = {
        "headers": _BASE_HEADERS,
        "follow_redirects": True,
        "timeout": 30,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)
