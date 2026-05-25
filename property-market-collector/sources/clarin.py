from typing import Optional

import httpx
from fastapi import HTTPException

from .base import BaseSource
from ._common import slugify, fetch_html, parse_ldjson, parse_body_text, extract_href_urls

_BASE = "https://www.inmuebles.clarin.com"

_TIPO_PATH = {
    "departamento": "departamentos",
    "casa": "casas",
    "ph": "ph",
    "local": "locales",
}


class ClarinSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "inmuebles.clarin.com" in url

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        html = await fetch_html(url, client, "Clarin Inmuebles")
        result = parse_ldjson(html)
        if not result:
            result = parse_body_text(html)
        if not result:
            raise HTTPException(422, "No se pudieron extraer datos de Clarin Inmuebles")
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
        # URL pattern: /departamentos/alquiler?zona=palermo
        tipo_path = _TIPO_PATH.get(tipo, tipo + "s")
        slug_ubi = slugify(ubicacion)
        urls: list[str] = []

        for page in range(1, paginas + 1):
            params: dict = {"zona": slug_ubi}
            if page > 1:
                params["pagina"] = page
            if precio_min:
                params["precio_minimo"] = precio_min
            if precio_max:
                params["precio_maximo"] = precio_max
            if ambientes_min:
                params["ambientes"] = ambientes_min
            if superficie_min:
                params["superficie_minima"] = superficie_min

            search_url = f"{_BASE}/{tipo_path}/{operacion}"
            try:
                html = await fetch_html(search_url, client, "Clarin Inmuebles", params=params)
            except HTTPException:
                continue

            # Listing URLs contain a numeric ID
            page_urls = extract_href_urls(html, _BASE, r"^/propiedades/.*-\d+")
            if not page_urls:
                page_urls = extract_href_urls(html, _BASE, r"^/[a-z][a-z0-9/-]+-\d+\.html")
            if not page_urls:
                break
            urls.extend(page_urls)

        return urls
