import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from .base import BaseSource
from ._common import fetch_html, extract_href_urls

_BASE = "https://inmuebles.lacapital.com.ar"
_SEARCH_URL = f"{_BASE}/buscar-propiedades/"

# La Capital usa términos propios para tipo y operación
_TIPO_MAP = {
    "departamento": "Departamento",
    "casa": "Casa",
    "ph": "PH",
    "local": "Local",
}
_OP_MAP = {
    "alquiler": "Alquiler",
    "venta": "Venta",
}


def _dormitorios_param(ambientes_min: int) -> str:
    # En Argentina: 2 ambientes = 1 dormitorio, 3 ambientes = 2 dormitorios, etc.
    d = max(1, ambientes_min - 1)
    return f"{d} dormitorio{'s' if d > 1 else ''}"


def _parse_lacapital(html: str) -> dict:
    """
    La Capital no usa JSON-LD. Los datos están en <p class="miniinfo">
    con íconos de imagen como marcadores de campo.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    def _span_text(icon_fragment: str) -> str:
        img = soup.find("img", src=re.compile(icon_fragment))
        if img:
            span = img.find_parent("span")
            if span:
                return span.get_text(strip=True)
        return ""

    # Precio: icono ico_precio_us.png → número en USD
    price_text = _span_text(r"ico_precio_us")
    if price_text:
        m = re.search(r"[\d.,]+", price_text)
        if m:
            try:
                val = int(float(m.group(0).replace(".", "").replace(",", "")))
                if val > 100:
                    result["precio"] = val
            except (ValueError, TypeError):
                pass

    # Fallback: meta description contiene "u$s49700"
    if "precio" not in result:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            m = re.search(r"u\$s\s*([\d.,]+)", meta.get("content", ""), re.I)
            if m:
                try:
                    val = int(float(m.group(1).replace(".", "").replace(",", "")))
                    if val > 100:
                        result["precio"] = val
                except (ValueError, TypeError):
                    pass

    # Superficie cubierta: icono ico_mt2cub.png
    surf_text = _span_text(r"ico_mt2cub")
    if surf_text:
        m = re.search(r"([\d.,]+)", surf_text)
        if m:
            try:
                result["superficie_cubierta"] = float(m.group(1).replace(",", "."))
            except (ValueError, TypeError):
                pass

    # Cochera: icono ico_cocheras.png, valor "--" significa sin cochera
    coch_text = _span_text(r"ico_cocheras")
    if coch_text and coch_text != "--":
        m = re.search(r"\d+", coch_text)
        if m and int(m.group(0)) > 0:
            result["cochera"] = True

    # Dirección: icono ico_dire.png
    dir_text = _span_text(r"ico_dire")
    if dir_text:
        result["direccion"] = dir_text

    # Fallback dirección desde JS: geo_propiedades = [{..., ubicacion: '...', ...}]
    if "direccion" not in result:
        m = re.search(r"ubicacion\s*:\s*'([^']+)'", html)
        if m:
            result["direccion"] = m.group(1)

    og = soup.find("meta", property="og:image")
    if og:
        content = og.get("content", "").strip()
        if content.startswith("http"):
            result["imagen_url"] = content

    return result


class LacapitalSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "lacapital.com.ar" in url

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        html = await fetch_html(url, client, "La Capital")
        result = _parse_lacapital(html)
        if not result:
            raise HTTPException(422, "No se pudieron extraer datos de La Capital")
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
        # La Capital no filtra por ubicación vía URL (devuelve 500 si se pasa localidad=valor)
        # El portal cubre principalmente Rosario y alrededores
        params_base: dict = {
            "operacion_hidden": _OP_MAP.get(operacion, operacion.title()),
            "inmueble_hidden": _TIPO_MAP.get(tipo, tipo.title()),
            "cocheras": "",
            "banios": "",
            "m2_totales": "",
            "m2_cubiertos": superficie_min or "",
            "precio": "",
            "expensas": "",
            "q": "",
            "sub_tipo_inmueble": "",
            "localidad": "",  # debe ir vacío — valor lleno devuelve HTTP 500
            "zona": "",
        }
        if ambientes_min and ambientes_min > 1:
            params_base["dormitorios"] = _dormitorios_param(ambientes_min)
        else:
            params_base["dormitorios"] = ""

        urls: list[str] = []

        for page in range(1, paginas + 1):
            params = {**params_base, "page": page}
            try:
                html = await fetch_html(_SEARCH_URL, client, "La Capital", params=params)
            except HTTPException:
                continue

            # Listing URLs: /inmuebles/{operacion}/{tipo}/{slug}_{id}/
            page_urls = extract_href_urls(html, _BASE, r"^/inmuebles/[a-z-]+/[a-z-]+/[a-z0-9-]+_\d+")
            if not page_urls:
                break
            urls.extend(page_urls)

        return urls
