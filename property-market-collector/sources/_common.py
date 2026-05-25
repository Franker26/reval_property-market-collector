"""Shared parsing utilities reused across portal scrapers."""
import json
import re
import unicodedata
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from . import browser as _browser

_TIPO_MAP = {
    "departamento": "Departamento",
    "casa": "Casa",
    "ph": "PH",
    "local": "Local",
    "local comercial": "Local",
    "oficina": "Local",
    "apartment": "Departamento",
    "house": "Casa",
    "singlefamilyresidence": "Casa",
}

_ORI_MAP = {
    "n": "Norte", "norte": "Norte",
    "s": "Sur", "sur": "Sur",
    "e": "Este", "este": "Este",
    "o": "Oeste", "oeste": "Oeste",
    "interno": "Interno",
    "ne": "Norte", "no": "Norte",
    "se": "Sur", "so": "Sur",
}


def slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower().strip()).strip("-")


async def fetch_html(
    url: str,
    client: httpx.AsyncClient,
    portal: str,
    params: dict | None = None,
) -> str:
    try:
        r = await client.get(url, params=params)
        if r.status_code == 403:
            return await _browser.fetch_rendered(str(r.url))
        r.raise_for_status()
        return r.text
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Error al acceder a {portal}: {e}")


def parse_ldjson(html: str) -> dict:
    """Extract listing data from JSON-LD scripts."""
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}
    upload_date = None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        schema_type = d.get("@type", "").lower()
        if schema_type not in (
            "apartment", "house", "singlefamilyresidence",
            "realestatelisting", "product",
        ):
            continue

        if "direccion" not in result:
            addr = d.get("address", {})
            if isinstance(addr, dict):
                street = addr.get("streetAddress", "").strip()
                region = addr.get("addressRegion", "").strip()
                if street:
                    result["direccion"] = f"{street}, {region}".strip(", ") if region else street

        if "precio" not in result:
            offers = d.get("offers", {})
            if isinstance(offers, dict):
                price = offers.get("price")
                currency = offers.get("priceCurrency", "USD")
                if price and currency == "USD":
                    try:
                        result["precio"] = int(float(price))
                    except (ValueError, TypeError):
                        pass

        if "superficie_cubierta" not in result:
            fs = d.get("floorSize", {})
            if isinstance(fs, dict) and fs.get("value"):
                try:
                    result["superficie_cubierta"] = float(fs["value"])
                except (ValueError, TypeError):
                    pass

        if "tipo" not in result and schema_type in _TIPO_MAP:
            result["tipo"] = _TIPO_MAP[schema_type]

        for key in ("datePosted", "datePublished", "uploadDate"):
            if d.get(key):
                upload_date = d[key]
                break

    if upload_date and "dias_mercado" not in result:
        try:
            pub = datetime.fromisoformat(upload_date.replace("Z", "+00:00"))
            result["dias_mercado"] = max(0, (datetime.now(timezone.utc) - pub).days)
        except Exception:
            pass

    if "imagen_url" not in result:
        img = extract_og_image(html)
        if img:
            result["imagen_url"] = img

    return result


def parse_body_text(html: str) -> dict:
    """Fallback extraction from raw page text using regex."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.get_text(" ", strip=True)
    result: dict = {}

    for m in re.finditer(r"(?:USD|U\$S|U\$D)\s*[\$]?\s*([\d.,]+)", body, re.I):
        try:
            val = int(float(m.group(1).replace(".", "").replace(",", "")))
            if 1_000 < val < 100_000_000:
                result["precio"] = val
                break
        except (ValueError, TypeError):
            pass

    m_cub = re.search(r"([\d.,]+)\s*m[²2]?\s*(?:cubiertos?|cubier)", body, re.I)
    m_semi = re.search(r"([\d.,]+)\s*m[²2]?\s*(?:semicubiertos?|semi)", body, re.I)
    m_tot = re.search(r"([\d.,]+)\s*m[²2]?\s*(?:totales?|total)", body, re.I)
    if m_cub:
        result["superficie_cubierta"] = float(m_cub.group(1).replace(",", "."))
    elif m_tot:
        result["superficie_cubierta"] = float(m_tot.group(1).replace(",", "."))
    if m_semi:
        result["superficie_semicubierta"] = float(m_semi.group(1).replace(",", "."))

    if re.search(r"\bcochera\b", body, re.I):
        result["cochera"] = True
    if re.search(r"\b(pileta|piscina)\b", body, re.I):
        result["pileta"] = True

    m_ant = re.search(r"(\d+)\s*años?\s*de\s*antig[uü]edad", body, re.I)
    if not m_ant:
        m_ant = re.search(r"antig[uü]edad[^\d]{0,10}(\d+)\s*años?", body, re.I)
    if m_ant:
        result["antiguedad"] = int(m_ant.group(1))

    m_ori = re.search(r"orientaci[oó]n\s*:?\s*([A-Za-z]+)", body, re.I)
    if m_ori:
        key = m_ori.group(1).lower()
        if key in _ORI_MAP:
            result["orientacion"] = _ORI_MAP[key]

    m_piso = re.search(r"piso\s*:?\s*(\d+)", body, re.I)
    if m_piso:
        result["piso"] = int(m_piso.group(1))

    if "imagen_url" not in result:
        img = extract_og_image(html)
        if img:
            result["imagen_url"] = img

    return result


def extract_og_image(html: str) -> str | None:
    """Return the og:image URL from a page, or None if absent."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    if tag:
        content = tag.get("content", "").strip()
        if content.startswith("http"):
            return content
    return None


def extract_href_urls(html: str, base_url: str, href_re: str) -> list[str]:
    """Collect <a href> links matching href_re, deduped, with absolute URLs."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    urls: list[str] = []
    compiled = re.compile(href_re)
    for a in soup.find_all("a", href=compiled):
        href = a["href"].split("?")[0].split("#")[0]
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = base_url.rstrip("/") + href
        else:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
