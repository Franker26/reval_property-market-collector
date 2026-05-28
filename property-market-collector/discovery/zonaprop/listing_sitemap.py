"""
discovery.zonaprop.listing_sitemap
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Descarga y parsea los sitemaps de páginas de listado de Zonaprop
(sitemap_list_https_*.xml) y emite las URLs de búsqueda filtrada que
se usarán como base_url en el discovery full-catalog.

Completamente desacoplado de la extracción — no llama a /extract
ni toca los sitemaps de propiedades (sitemap_prop_https_*.xml.gz).
"""

from __future__ import annotations

import gzip
import logging
import re
from pathlib import Path
from typing import Generator
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

SITEMAP_INDEX_URL = "https://www.zonaprop.com.ar/sitemaps_https.xml"
LIST_SITEMAP_PATTERN = re.compile(r"sitemap_list_https_", re.IGNORECASE)

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_OP_TYPE_RE = re.compile(
    r"\b(alquiler-temporal|alquiler|venta)\b", re.IGNORECASE
)


# ── Helpers XML (mismos que zonaprop_sitemap.py) ──────────────────────────────


def _parse_xml_bytes(data: bytes) -> ET.Element:
    try:
        decompressed = gzip.decompress(data)
    except (OSError, EOFError):
        decompressed = data
    return ET.fromstring(decompressed)


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    results = root.findall(f"sm:{tag}", _NS)
    if not results:
        results = root.findall(tag)
    return results


def _find_one(element: ET.Element, tag: str) -> ET.Element | None:
    result = element.find(f"sm:{tag}", _NS)
    if result is None:
        result = element.find(tag)
    return result


# ── Fetch sitemaps ────────────────────────────────────────────────────────────


def get_list_sitemap_urls(client: httpx.Client) -> list[str]:
    """
    Descarga el índice y devuelve las URLs de los sitemaps de listing pages
    (sitemap_list_https_*.xml).
    """
    log.info("listing_sitemap: fetching sitemap index %s", SITEMAP_INDEX_URL)
    resp = client.get(SITEMAP_INDEX_URL)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    urls: list[str] = []
    for sitemap_el in _find_all(root, "sitemap"):
        loc_el = _find_one(sitemap_el, "loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if LIST_SITEMAP_PATTERN.search(loc):
            urls.append(loc)

    log.info("listing_sitemap: %d sitemaps de listing encontrados", len(urls))
    return urls


# ── Parseo de listing URLs ────────────────────────────────────────────────────


def parse_listing_urls(raw_bytes: bytes, filename: str) -> Generator[str, None, None]:
    """
    Parsea un XML de listing sitemap (posiblemente gzip) y yields cada <loc>.
    """
    try:
        root = _parse_xml_bytes(raw_bytes)
    except (ET.ParseError, OSError) as exc:
        log.error("listing_sitemap: no se pudo parsear %s — %s", filename, exc)
        return

    count = 0
    for url_el in _find_all(root, "url"):
        loc_el = _find_one(url_el, "loc")
        if loc_el is not None and loc_el.text:
            yield loc_el.text.strip()
            count += 1

    log.info("listing_sitemap: %s → %d URLs", filename, count)


# ── Inferir operation_type desde URL ─────────────────────────────────────────


def infer_operation_type(url: str) -> str:
    """
    Extrae "alquiler-temporal", "alquiler" o "venta" de la URL.
    Default "venta" si no se encuentra ninguno.
    """
    m = _OP_TYPE_RE.search(url)
    if m:
        return m.group(1).lower()
    return "venta"


# ── Función principal ─────────────────────────────────────────────────────────


def iter_all_listing_urls(
    local_paths: list[str | Path] | None = None,
) -> Generator[str, None, None]:
    """
    Emite todas las URLs de listing pages de los sitemaps.

    Si se pasan `local_paths`: lee los archivos XML locales (desarrollo/test).
    Si no: descarga los sitemaps desde el índice online.
    """
    if local_paths:
        for path in local_paths:
            p = Path(path)
            log.info("listing_sitemap: leyendo archivo local %s", p.name)
            raw = p.read_bytes()
            yield from parse_listing_urls(raw, p.name)
        return

    with httpx.Client(headers=_HTTP_HEADERS, follow_redirects=True, timeout=60) as client:
        try:
            sitemap_urls = get_list_sitemap_urls(client)
        except Exception as exc:
            log.error("listing_sitemap: no se pudo obtener el índice: %s", exc)
            return

        for sitemap_url in sitemap_urls:
            filename = sitemap_url.rsplit("/", 1)[-1]
            log.info("listing_sitemap: descargando %s", sitemap_url)
            try:
                resp = client.get(sitemap_url)
                # Aceptar 404 con content (comportamiento conocido del servidor)
                raw = resp.content
                if not raw:
                    log.warning("listing_sitemap: %s vacío, saltando", filename)
                    continue
            except Exception as exc:
                log.error("listing_sitemap: error descargando %s — %s", sitemap_url, exc)
                continue

            yield from parse_listing_urls(raw, filename)
