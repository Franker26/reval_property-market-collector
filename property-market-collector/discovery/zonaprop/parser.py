"""
discovery.zonaprop.parser
~~~~~~~~~~~~~~~~~~~~~~~~~
Extrae URLs de publicaciones desde el HTML de una página de listado de Zonaprop.

Estrategia (por orden de prioridad):
  1. __NEXT_DATA__ JSON embebido (Next.js) — más completo y estructurado.
  2. Fallback BeautifulSoup sobre <a href> — cubre casos donde el JSON no está.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from .models import DiscoveryRecord

log = logging.getLogger(__name__)

_BASE_DOMAIN = "https://www.zonaprop.com.ar"

# Mismo regex que zonaprop_sitemap.py
_EXTERNAL_ID_RE = re.compile(r"-(\d{6,})\.html$")

# Patrones de URLs de publicaciones finales
_PROP_PATH_RE = re.compile(r"/propiedades/(?:clasificado|emprendimiento)/")
_PROP_HREF_RE = re.compile(r"/propiedades/(?:clasificado|emprendimiento)/.*-\d{6,}\.html")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_external_id(url: str) -> str | None:
    m = _EXTERNAL_ID_RE.search(url)
    return m.group(1) if m else None


def _normalize_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return _BASE_DOMAIN + href
    return href


def _looks_like_listing_url(url: str) -> bool:
    return bool(_PROP_PATH_RE.search(url)) and bool(_EXTERNAL_ID_RE.search(url))


# ── Extracción desde __NEXT_DATA__ ────────────────────────────────────────────


def _walk_for_urls(node: Any, found: set[str]) -> None:
    """Recorre recursivamente el árbol JSON buscando URLs de publicaciones."""
    if isinstance(node, str):
        if _looks_like_listing_url(node):
            found.add(_normalize_url(node))
        return
    if isinstance(node, dict):
        # Buscar campos url/URL directamente en el dict
        for key in ("url", "URL", "postingUrl", "href"):
            val = node.get(key)
            if isinstance(val, str) and _looks_like_listing_url(val):
                found.add(_normalize_url(val))
        # Continuar recorriendo valores
        for val in node.values():
            _walk_for_urls(val, found)
        return
    if isinstance(node, list):
        for item in node:
            _walk_for_urls(item, found)


def _extract_from_next_data(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if not script or not script.string:
        return []
    try:
        data = json.loads(script.string)
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("parser: error al parsear __NEXT_DATA__: %s", exc)
        return []

    found: set[str] = set()
    _walk_for_urls(data, found)
    log.debug("parser: __NEXT_DATA__ → %d URLs encontradas", len(found))
    return list(found)


# ── Fallback HTML ─────────────────────────────────────────────────────────────


def _extract_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()
    for a in soup.find_all("a", href=_PROP_HREF_RE):
        href = a.get("href", "")
        url = _normalize_url(href.split("?")[0].split("#")[0])
        if _looks_like_listing_url(url):
            found.add(url)
    log.debug("parser: HTML fallback → %d URLs encontradas", len(found))
    return list(found)


# ── Función pública ───────────────────────────────────────────────────────────


def parse_listing_page(
    html: str,
    search_url: str,
    page: int,
    operation_type: str,
    discovered_at: str,
) -> list[DiscoveryRecord]:
    """
    Extrae registros de discovery desde el HTML de una página de listado.

    Intenta primero __NEXT_DATA__; si no produce resultados, usa BeautifulSoup.
    Deduplica por external_id dentro de la página y descarta URLs sin ID válido.
    """
    raw_urls = _extract_from_next_data(html)
    method = "__NEXT_DATA__"

    if not raw_urls:
        raw_urls = _extract_from_html(html)
        method = "html_fallback"

    seen_ids: set[str] = set()
    records: list[DiscoveryRecord] = []

    for url in raw_urls:
        ext_id = _extract_external_id(url)
        if ext_id is None:
            log.debug("parser: sin external_id, omitiendo %s", url)
            continue
        if ext_id in seen_ids:
            continue
        seen_ids.add(ext_id)
        records.append(
            DiscoveryRecord(
                source="zonaprop",
                url=url,
                external_id=ext_id,
                operation_type=operation_type,
                discovery_method="listing_page",
                search_url=search_url,
                page=page,
                discovered_at=discovered_at,
            )
        )

    log.debug(
        "parser: página %d → %d registros (método: %s)", page, len(records), method
    )
    return records
