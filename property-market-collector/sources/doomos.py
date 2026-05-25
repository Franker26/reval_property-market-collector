import re
from typing import Optional

import httpx
from fastapi import HTTPException

from .base import BaseSource
from ._common import fetch_html, parse_ldjson, parse_body_text, slugify
from . import browser as _browser

_BASE = "https://ar.doomos.com"

_TIPO_SLUG = {
    "departamento": "departamento",
    "casa": "casa",
    "ph": "ph",
    "local": "local",
}


class DoomosSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "ar.doomos.com" in url

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        # Doomos sirve HTML completo con JSON-LD — no necesita Playwright
        html = await fetch_html(url, client, "Doomos")
        result = parse_ldjson(html)
        if not result:
            result = parse_body_text(html)
        if not result:
            raise HTTPException(422, "No se pudieron extraer datos de Doomos")
        return result

    async def search_listings(
        self,
        operacion: str,
        tipo: str,
        ubicacion: str,
        precio_min: Optional[int],
        precio_max: Optional[int],
        ambientes_min: Optional[int],
        ambientes_max: Optional[int],
        superficie_min: Optional[int],
        superficie_max: Optional[int],
        paginas: int,
        client: httpx.AsyncClient,
    ) -> list[str]:
        tipo_slug = _TIPO_SLUG.get(tipo, tipo)
        slug_ubi = slugify(ubicacion)
        urls: list[str] = []

        for page in range(1, paginas + 1):
            # Doomos es SPA — la página de resultados requiere Playwright para
            # que los filtros de ubicación se apliquen correctamente.
            # Formato confirmado: /{tipo}-{op}-{slug-ubicacion}
            search_url = f"{_BASE}/{tipo_slug}-{operacion}-{slug_ubi}"
            if page > 1:
                search_url += f"?pagina={page}"

            try:
                html = await _browser.fetch_rendered(
                    search_url,
                    wait_selector="[class*='card'], [class*='listing'], article, a[href*='/propiedad/']",
                    timeout=30_000,
                )
            except Exception:
                continue

            page_urls = _extract_listing_urls(html)
            if not page_urls:
                break
            urls.extend(page_urls)

        return urls


def _extract_listing_urls(html: str) -> list[str]:
    """Extrae hrefs /propiedad/{slug} del HTML renderizado."""
    seen: set[str] = set()
    urls: list[str] = []

    for m in re.finditer(r'href="(/propiedad/[^"]+)"', html):
        href = m.group(1).split("?")[0].split("#")[0]
        url = _BASE + href
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls
