"""
discovery.zonaprop_sitemap
~~~~~~~~~~~~~~~~~~~~~~~~~~
Lee el sitemap index público de Zonaprop, filtra los sitemaps de
propiedades (sitemap_prop_https_*.xml.gz), y genera un registro por
cada URL de publicación descubierta.

Completamente desacoplado de la capa de extracción — no llama a /extract
ni parsea páginas de detalle de propiedades.
"""

from __future__ import annotations

import gzip
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Generator
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

SITEMAP_INDEX_URL = "https://www.zonaprop.com.ar/sitemaps_https.xml"
PROP_SITEMAP_PATTERN = re.compile(r"sitemap_prop_https_", re.IGNORECASE)

# Extrae el external_id numérico del final de la URL.
# Ej: ".../veclcain-casa-historica-58770807.html" → "58770807"
_EXTERNAL_ID_RE = re.compile(r"-(\d{6,})\.html$")

# Namespace estándar de sitemaps
_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# UTC-3 (Argentina)
_ART = timezone(timedelta(hours=-3))

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RevalBot/1.0; +https://reval.com.ar/bot)"
    ),
    "Accept": "application/xml, text/xml, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def extract_external_id(url: str) -> str | None:
    """Devuelve el external_id numérico embebido al final de una URL de Zonaprop."""
    m = _EXTERNAL_ID_RE.search(url)
    return m.group(1) if m else None


def _parse_xml_bytes(data: bytes) -> ET.Element:
    """Parsea bytes (posiblemente comprimidos con gzip) en un Element XML."""
    try:
        decompressed = gzip.decompress(data)
    except (OSError, EOFError):
        # No es gzip — tratar como XML crudo
        decompressed = data
    return ET.fromstring(decompressed)


def _find_all(root: ET.Element, tag: str) -> list[ET.Element]:
    """
    Busca elementos por tag soportando tanto XML con namespace como sin él.
    Intenta primero con el namespace estándar de sitemaps; si no encuentra
    nada, intenta sin namespace (algunos servidores omiten el xmlns).
    """
    results = root.findall(f"sm:{tag}", _NS)
    if not results:
        results = root.findall(tag)
    return results


def _find_one(element: ET.Element, tag: str) -> ET.Element | None:
    """Igual que _find_all pero devuelve el primer resultado o None."""
    result = element.find(f"sm:{tag}", _NS)
    if result is None:
        result = element.find(tag)
    return result


# ── Funciones de fetch ────────────────────────────────────────────────────────


def fetch_sitemap_index(client: httpx.Client) -> ET.Element:
    """
    Descarga y parsea el sitemap index XML.

    Raises:
        httpx.HTTPError: si la petición de red falla.
        ET.ParseError: si la respuesta no es XML válido.
    """
    log.info("discovery.zonaprop: fetching sitemap index %s", SITEMAP_INDEX_URL)
    resp = client.get(SITEMAP_INDEX_URL)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    log.info("discovery.zonaprop: sitemap index OK (%d bytes)", len(resp.content))
    return root


def get_property_sitemap_urls(index_root: ET.Element) -> list[str]:
    """
    Extrae del sitemap index solo los sitemaps de propiedades
    (nombre contiene ``sitemap_prop_https_``).
    """
    urls: list[str] = []

    for sitemap_el in _find_all(index_root, "sitemap"):
        loc_el = _find_one(sitemap_el, "loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if PROP_SITEMAP_PATTERN.search(loc):
            urls.append(loc)

    log.info(
        "discovery.zonaprop: %d entradas sitemap_prop_https_ encontradas en el index",
        len(urls),
    )
    return urls


def fetch_gzipped_sitemap(url: str, client: httpx.Client) -> bytes:
    """
    Descarga un sitemap (posiblemente comprimido con gzip) y devuelve los bytes crudos.

    Raises:
        httpx.HTTPError: en errores de red o HTTP.
    """
    log.info("discovery.zonaprop: fetching %s", url)
    resp = client.get(url)
    resp.raise_for_status()
    log.debug("discovery.zonaprop: %s → %d bytes", url, len(resp.content))
    return resp.content


def parse_property_urls(
    raw_bytes: bytes,
    sitemap_filename: str,
    discovered_at: str,
) -> Generator[dict, None, None]:
    """
    Parsea un sitemap XML (posiblemente gzip) y genera un registro por cada
    ``<url>`` que corresponda a una publicación final de Zonaprop.

    Cada registro generado contiene:
        source, url, external_id, lastmod, discovered_at, discovery_source

    Las URLs de las que no se pueda extraer external_id se loguean como
    warning y se omiten sin interrumpir el proceso.
    """
    try:
        root = _parse_xml_bytes(raw_bytes)
    except (ET.ParseError, OSError) as exc:
        log.error(
            "discovery.zonaprop: no se pudo parsear %s — %s", sitemap_filename, exc
        )
        return

    for url_el in _find_all(root, "url"):
        loc_el = _find_one(url_el, "loc")
        lastmod_el = _find_one(url_el, "lastmod")

        if loc_el is None or not loc_el.text:
            continue

        loc = loc_el.text.strip()

        # Solo páginas de detalle de propiedades (contienen /propiedades/ en la ruta)
        if "/propiedades/" not in loc:
            continue

        external_id = extract_external_id(loc)
        if external_id is None:
            log.warning(
                "discovery.zonaprop: no se pudo extraer external_id de %s — omitiendo",
                loc,
            )
            continue

        lastmod = (
            lastmod_el.text.strip()
            if (lastmod_el is not None and lastmod_el.text)
            else None
        )

        yield {
            "source": "zonaprop",
            "url": loc,
            "external_id": external_id,
            "lastmod": lastmod,
            "discovered_at": discovered_at,
            "discovery_source": sitemap_filename,
        }


# ── Pipeline principal ────────────────────────────────────────────────────────


def discover_zonaprop_urls() -> tuple[list[dict], dict]:
    """
    Pipeline de discovery completo:

    1. Descarga el sitemap index.
    2. Filtra entradas ``sitemap_prop_https_*``.
    3. Por cada una, descarga, descomprime y parsea URLs.
    4. Deduplica por ``external_id``.
    5. Devuelve (records, stats).

    Returns:
        records: lista de dicts (una por URL única de publicación).
        stats:   dict con contadores resumen.
    """
    discovered_at = datetime.now(_ART).isoformat(timespec="seconds")

    stats: dict = {
        "sitemap_prop_found": 0,
        "sitemap_prop_failed": 0,
        "urls_parsed": 0,
        "urls_valid": 0,
        "duplicates_discarded": 0,
    }

    with httpx.Client(headers=_HTTP_HEADERS, follow_redirects=True, timeout=60) as client:

        # ── 1. Sitemap index ───────────────────────────────────────────────────
        try:
            index_root = fetch_sitemap_index(client)
        except Exception as exc:
            log.error(
                "discovery.zonaprop: FATAL — no se pudo obtener el sitemap index: %s", exc
            )
            return [], stats

        # ── 2. Filtrar sitemaps de propiedades ────────────────────────────────
        prop_sitemap_urls = get_property_sitemap_urls(index_root)
        stats["sitemap_prop_found"] = len(prop_sitemap_urls)

        if not prop_sitemap_urls:
            log.warning(
                "discovery.zonaprop: no se encontraron entradas sitemap_prop_https_ en el index"
            )
            return [], stats

        # ── 3–4. Fetch, parseo, deduplicación ─────────────────────────────────
        seen_external_ids: set[str] = set()
        seen_urls: set[str] = set()
        records: list[dict] = []

        for sitemap_url in prop_sitemap_urls:
            sitemap_filename = sitemap_url.rsplit("/", 1)[-1]

            try:
                raw_bytes = fetch_gzipped_sitemap(sitemap_url, client)
            except Exception as exc:
                log.error(
                    "discovery.zonaprop: error al descargar %s — %s (continuando)",
                    sitemap_url,
                    exc,
                )
                stats["sitemap_prop_failed"] += 1
                continue

            sitemap_count = 0
            for record in parse_property_urls(raw_bytes, sitemap_filename, discovered_at):
                stats["urls_parsed"] += 1
                sitemap_count += 1

                ext_id = record["external_id"]
                url = record["url"]

                # Deduplicar: se prefiere la primera ocurrencia (sitemap más bajo numerado)
                if ext_id in seen_external_ids or url in seen_urls:
                    stats["duplicates_discarded"] += 1
                    continue

                seen_external_ids.add(ext_id)
                seen_urls.add(url)
                records.append(record)
                stats["urls_valid"] += 1

            log.info(
                "discovery.zonaprop: %s → %d URLs parseadas", sitemap_filename, sitemap_count
            )

    return records, stats
