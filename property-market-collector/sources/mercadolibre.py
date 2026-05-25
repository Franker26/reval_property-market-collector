import json
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from .base import BaseSource
from . import browser as _browser

_ML_BASE = "https://inmuebles.mercadolibre.com.ar"

_ML_TIPO_PATH = {
    "departamento": "departamentos",
    "casa": "casas",
    "ph": "ph",
    "local": "locales-comerciales",
}

_ML_OPERACION_PATH = {
    "alquiler": "alquiler",
    "venta": "venta",
}

_TIPO_MAP = {
    "departamento": "Departamento",
    "casa": "Casa",
    "ph": "PH",
    "local": "Local",
    "local comercial": "Local",
    "oficina": "Local",
}

_ORI_MAP = {
    "norte": "Norte", "sur": "Sur", "este": "Este", "oeste": "Oeste",
    "interno": "Interno", "n": "Norte", "s": "Sur", "e": "Este", "o": "Oeste",
    "ne": "Norte", "no": "Norte", "se": "Sur", "so": "Sur",
}




def _parse_rendered(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    # --- JSON-LD ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
        except Exception:
            continue
        schema_type = d.get("@type", "")
        if schema_type not in ("Product", "Apartment", "House", "RealEstateListing"):
            continue
        offers = d.get("offers", {})
        if isinstance(offers, dict):
            price = offers.get("price")
            currency = offers.get("priceCurrency", "USD")
            if price and currency == "USD":
                try:
                    result["precio"] = int(float(price))
                except (ValueError, TypeError):
                    pass
        addr = d.get("address", {})
        if isinstance(addr, dict):
            street = addr.get("streetAddress", "").strip()
            region = addr.get("addressRegion", "").strip()
            if street:
                result["direccion"] = f"{street}, {region}".strip(", ") if region else street
        for key in ("datePosted", "datePublished"):
            pub_str = d.get(key)
            if isinstance(pub_str, str):
                try:
                    pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    result["dias_mercado"] = max(0, (datetime.now(timezone.utc) - pub).days)
                except Exception:
                    pass
                break
        break

    # --- Price from DOM ---
    if "precio" not in result:
        # Andes money amount components (ML design system)
        for container in soup.select(".andes-money-amount, .price-tag, [class*='price']"):
            text = container.get_text(" ", strip=True)
            # USD price
            m = re.search(r"USD\s*([\d.,]+)", text, re.I)
            if not m:
                m = re.search(r"U\$S\s*([\d.,]+)", text, re.I)
            if m:
                try:
                    val = int(float(m.group(1).replace(".", "").replace(",", "")))
                    if 1_000 < val < 100_000_000:
                        result["precio"] = val
                        break
                except (ValueError, TypeError):
                    pass

    body_text = soup.get_text(" ", strip=True)

    # --- Surface ---
    m2_cub = re.search(r"([\d.,]+)\s*m²?\s*(?:cubiertos?|cubier)", body_text, re.I)
    m2_semi = re.search(r"([\d.,]+)\s*m²?\s*(?:semicubiertos?|semi)", body_text, re.I)
    m2_tot = re.search(r"([\d.,]+)\s*m²?\s*(?:totales?|total)", body_text, re.I)
    if m2_cub:
        result["superficie_cubierta"] = float(m2_cub.group(1).replace(",", "."))
    elif m2_tot:
        result["superficie_cubierta"] = float(m2_tot.group(1).replace(",", "."))
    if m2_semi:
        result["superficie_semicubierta"] = float(m2_semi.group(1).replace(",", "."))

    # --- Type ---
    if "tipo" not in result:
        for raw, mapped in _TIPO_MAP.items():
            if re.search(rf"\b{re.escape(raw)}\b", body_text, re.I):
                result["tipo"] = mapped
                break

    # --- Antigüedad ---
    m_ant = re.search(r"(\d+)\s*años?\s*de\s*antig[uü]edad", body_text, re.I)
    if not m_ant:
        m_ant = re.search(r"antig[uü]edad[^\d]{0,10}(\d+)\s*años?", body_text, re.I)
    if m_ant:
        result["antiguedad"] = int(m_ant.group(1))

    # --- Orientation ---
    m_ori = re.search(r"orientaci[oó]n\s*:?\s*([A-Za-z]+)", body_text, re.I)
    if m_ori:
        key = m_ori.group(1).lower()
        mapped = _ORI_MAP.get(key)
        if mapped:
            result["orientacion"] = mapped

    # --- Floor ---
    m_piso = re.search(r"piso\s*:?\s*(\d+)", body_text, re.I)
    if m_piso:
        result["piso"] = int(m_piso.group(1))

    # --- Cochera / pileta ---
    if re.search(r"\bcochera\b", body_text, re.I):
        result["cochera"] = True
    if re.search(r"\b(pileta|piscina)\b", body_text, re.I):
        result["pileta"] = True

    # --- Location fallback ---
    if "direccion" not in result:
        for sel in (".ui-pdp-media__title", ".location", "[class*='location']", "[class*='address']"):
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if 5 < len(t) < 120:
                    result["direccion"] = t
                    break

    # Clean ML address noise appended by the SPA
    if "direccion" in result:
        addr = result["direccion"]
        addr = re.sub(r"^Ubicaci[oó]n\s+", "", addr, flags=re.I)
        addr = re.sub(r"\s*Ver informaci[oó]n.*$", "", addr, flags=re.I)
        # Remove duplicate trailing segments (e.g. "Belgrano, Capital Federal, Capital Federal")
        parts = [p.strip() for p in addr.split(",")]
        seen: list[str] = []
        for p in parts:
            if p and p not in seen:
                seen.append(p)
        result["direccion"] = ", ".join(seen).strip()

    if "imagen_url" not in result:
        og = soup.find("meta", property="og:image")
        if og:
            content = og.get("content", "").strip()
            if content.startswith("http"):
                result["imagen_url"] = content

    return result


class MercadoLibreSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "mercadolibre.com.ar" in url or (
            "mercadolibre.com" in url and "MLA" in url
        )

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        try:
            html = await _browser.fetch_rendered(
                url,
                wait_selector=".andes-money-amount, .price-tag, [class*='price']",
            )
        except Exception as e:
            raise HTTPException(502, f"Error al renderizar MercadoLibre: {e}")

        result = _parse_rendered(html)
        if not result:
            raise HTTPException(422, "No se pudieron extraer datos de MercadoLibre")
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
        tipo_path = _ML_TIPO_PATH.get(tipo, tipo + "s")
        op_path = _ML_OPERACION_PATH.get(operacion, operacion)
        ubi_path = re.sub(r"[^a-z0-9]+", "-", ubicacion.lower().strip()).strip("-")

        # Construir segmento de filtros ML (path-based, orden importa)
        # Precio requiere sesión autenticada → se omite y se filtra post-extracción
        # Ambientes: _Ambientes_{n} soportado sin auth
        filter_segment = ""
        if ambientes_min:
            filter_segment += f"_Ambientes_{ambientes_min}"

        urls: list[str] = []
        items_per_page = 48

        for page in range(paginas):
            if page == 0:
                search_url = f"{_ML_BASE}/{tipo_path}/{op_path}/{ubi_path}/{filter_segment}"
            else:
                offset = page * items_per_page + 1
                search_url = f"{_ML_BASE}/{tipo_path}/{op_path}/{ubi_path}/{filter_segment}_Desde_{offset}_NoIndex_True"

            # ML es SPA — necesita renderizado igual que los listings individuales
            try:
                html = await _browser.fetch_rendered(
                    search_url,
                    wait_selector="li.ui-search-layout__item, [class*='poly-card']",
                    timeout=30_000,
                )
            except Exception:
                continue

            page_urls = _extract_ml_listing_urls(html)
            if not page_urls:
                break
            urls.extend(page_urls)

        return urls


def _extract_ml_listing_urls(html: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for m in re.finditer(r'href="(https?://[^"]*mercadolibre\.com\.ar[^"]*MLA[^"]*?)"', html):
        url = m.group(1).split("?")[0].split("#")[0]
        # Filtrar URLs de búsqueda/categoría — solo listings individuales con MLA-XXXXXXX
        if re.search(r"MLA-\d+", url) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
