"""Zonaprop scraper — extracción por URL."""
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag
from fastapi import HTTPException

from .base import BaseSource
from . import browser as _browser
from .models import Location, PropertyListing

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_TIPO_MAP = {
    "departamento": "Departamento",
    "casa": "Casa",
    "ph": "PH",
    "local": "Local",
    "local comercial": "Local",
    "oficina": "Oficina",
    "terreno": "Terreno",
    "apartment": "Departamento",
    "house": "Casa",
    "singlefamilyresidence": "Casa",
}

# icon class → (field_name, parse_type)
_ICON_FIELD_MAP: list[tuple[str, str, str]] = [
    ("icon-stotal",     "total_area",    "float"),
    ("icon-scubierta",  "covered_area",  "float"),
    ("icon-ssemi",      "uncovered_area","float"),
    ("icon-ambiente",   "ambiences",     "int"),
    ("icon-bano",       "bathrooms",     "int"),
    ("icon-cochera",    "garages",       "int"),
    ("icon-dormitorio", "bedrooms",      "int"),
    ("icon-toilette",   "toilettes",     "int"),
    ("icon-antiguedad", "age",           "age"),
    ("icon-orientacion","orientation",   "text"),
    ("icon-piso",       "floor",         "int"),
]

_AR_TIMEZONE = timezone(timedelta(hours=-3))

# ── Low-level helpers ─────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower().strip()).strip("-")


async def _fetch_html(url: str, _client: httpx.AsyncClient) -> str:
    """
    Zonaprop siempre se renderiza con Playwright: el seller card y otros
    bloques (publisher, expensas) son lazy-loaded via XHR y no aparecen
    en el HTML estático que devuelve httpx.
    Se espera el selector del seller para asegurar que el DOM esté completo.
    """
    try:
        return await _browser.fetch_rendered(
            url,
            wait_selector='[data-qa="linkMicrositioAnuncianteLeads"]',
        )
    except Exception as e:
        raise HTTPException(502, f"Error al acceder a Zonaprop: {e}")


def _external_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"-(\d{6,})\.html", url)
    return m.group(1) if m else None


def _ar_number(text: str) -> Optional[float]:
    """Parse Argentine-formatted number: dots=thousands, comma=decimal."""
    cleaned = text.replace(".", "").replace(",", ".")
    m = re.search(r"([\d]+(?:\.\d+)?)", cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ── HTML parsers ──────────────────────────────────────────────────────────────

def _parse_inline_script_vars(html: str) -> dict:
    """Extract antiquity and usersViews from inline JS block."""
    result: dict = {}

    m = re.search(r"const\s+antiquity\s*=\s*'([^']+)'", html)
    if m:
        text = m.group(1).strip()
        result["published_at_text"] = text
        dm = re.search(r"hace\s+(\d+)\s+días?", text, re.I)
        if dm:
            result["published_days_ago"] = int(dm.group(1))
        elif re.search(r"hoy", text, re.I):
            result["published_days_ago"] = 0
        elif re.search(r"ayer", text, re.I):
            result["published_days_ago"] = 1
        elif re.search(r"hace\s+(\d+)\s+horas?", text, re.I):
            result["published_days_ago"] = 0
        elif re.search(r"hace\s+(\d+)\s+mes", text, re.I):
            n = re.search(r"hace\s+(\d+)\s+mes", text, re.I)
            result["published_days_ago"] = int(n.group(1)) * 30 if n else None
        elif re.search(r"hace\s+(\d+)\s+años?", text, re.I):
            n = re.search(r"hace\s+(\d+)\s+años?", text, re.I)
            result["published_days_ago"] = int(n.group(1)) * 365 if n else None
        elif re.search(r"más\s+de\s+1\s+año", text, re.I):
            result["published_days_ago"] = 365

    m = re.search(r"const\s+usersViews\s*=\s*(\d+)", html)
    if m:
        result["views_count"] = int(m.group(1))

    return result


def _parse_icon_features(soup: BeautifulSoup) -> dict:
    """Parse numeric/text features from icon-based UI elements."""
    result: dict = {}

    for icon_class, field_name, parse_type in _ICON_FIELD_MAP:
        icon_el = soup.find("i", class_=icon_class)
        if not icon_el and icon_class == "icon-toilette":
            icon_el = soup.find("i", class_="icon-toilet")
        if not icon_el:
            continue

        container: Optional[Tag] = icon_el.find_parent("li") or icon_el.find_parent()
        if not container:
            continue

        text = _clean_text(container.get_text(" ", strip=True))

        if parse_type == "text":
            result[field_name] = text
        elif parse_type in ("int", "float"):
            val = _ar_number(text)
            if val is not None:
                result[field_name] = int(val) if parse_type == "int" else val
        elif parse_type == "age":
            if re.search(r"estrenar", text, re.I):
                result[field_name] = 0
            else:
                m = re.search(r"(\d+)", text)
                if m:
                    result[field_name] = int(m.group(1))

    return result


def _parse_price_block(soup: BeautifulSoup) -> dict:
    """Extract operation_type, currency and amount from the price block."""
    price_div = (
        soup.find(class_=re.compile(r"price-value", re.I))
        or soup.find(class_=re.compile(r"block-price-container", re.I))
        or soup.find(class_=re.compile(r"price-items", re.I))
    )
    if not price_div:
        return {}

    text = price_div.get_text(" ", strip=True)
    result: dict = {}

    op_m = re.search(r"^(alquiler\s+temporal|alquiler|venta)", text, re.I)
    if op_m:
        result["operation_type"] = op_m.group(1).strip().lower()

    price_m = re.search(r"(USD|ARS|\$)\s*([\d.,]+)", text, re.I)
    if price_m:
        sym = price_m.group(1).strip()
        currency = "USD" if sym.upper() == "USD" else "ARS"
        amount = _ar_number(price_m.group(2))
        if amount and amount > 0:
            result["currency"] = currency
            result["precio"] = int(amount)

    return result


def _parse_property_type(soup: BeautifulSoup) -> Optional[str]:
    """Extract property type from the h2 title element."""
    h2 = (
        soup.find("h2", class_=re.compile(r"title-type-sup-property", re.I))
        or soup.find("h2", class_=re.compile(r"title-h1-development", re.I))
    )
    if not h2:
        return None
    parts = [p.strip() for p in re.split(r"\s*[·•|]\s*", h2.get_text(" ", strip=True)) if p.strip()]
    if parts:
        return _TIPO_MAP.get(parts[0].lower(), parts[0])
    return None


def _parse_description_html(soup: BeautifulSoup) -> Optional[str]:
    """Extract description text from the posting description block."""
    desc_el: Optional[Tag] = None

    for qa in ("description", "posting-description", "property-description"):
        el = soup.find(attrs={"data-qa": qa})
        if el and isinstance(el, Tag):
            desc_el = el
            break

    if not desc_el:
        for pattern in (
            r"wrapper-description",
            r"description-module",
            r"posting.*description",
            r"propertyDescription",
        ):
            el = soup.find(class_=re.compile(pattern, re.I))
            if el and isinstance(el, Tag):
                desc_el = el
                break

    if not desc_el:
        return None

    for br in desc_el.find_all("br"):
        br.replace_with("\n")

    text = desc_el.get_text("\n", strip=True)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _parse_published_days(soup: BeautifulSoup) -> Optional[int]:
    """DOM fallback for published days (used when inline JS vars not found)."""
    nav_str = soup.find(string=re.compile(r"Publicado hace|Publicado hoy|Publicado ayer", re.I))
    pub_el = None
    if nav_str:
        parent = nav_str.find_parent()
        if parent and parent.name not in ("script", "style"):
            pub_el = parent

    if not pub_el:
        el = soup.find(class_=re.compile(r"post-antiquity|antiquity-views", re.I))
        if el and isinstance(el, Tag) and el.name not in ("script", "style"):
            pub_el = el

    if not pub_el:
        return None

    for child in pub_el.find_all(["script", "style"]):
        child.decompose()

    text = _clean_text(pub_el.get_text(" ", strip=True))

    dm = re.search(r"hace\s+(\d+)\s+días?", text, re.I)
    if dm:
        return int(dm.group(1))
    if re.search(r"hoy|ayer", text, re.I):
        return 0 if "hoy" in text.lower() else 1
    if re.search(r"hace\s+(\d+)\s+horas?", text, re.I):
        return 0
    m = re.search(r"hace\s+(\d+)\s+mes", text, re.I)
    if m:
        return int(m.group(1)) * 30
    return None


def _seller_type_from_href(href: str) -> Optional[str]:
    """Derive seller type from the profile URL path segment."""
    href = href.lower()
    if "/inmobiliarias/" in href:
        return "inmobiliaria"
    if "/corredores/" in href:
        return "corredor"
    if "/desarrolladores/" in href or "/constructoras/" in href:
        return "desarrollador"
    if "/particulares/" in href:
        return "particular"
    return None


def _parse_seller_html(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (seller_name, seller_type).

    Strategy (in priority order):
      1. <a data-qa="linkMicrositioAnuncianteLeads"> — most stable
      2. <a data-qa="..."> regex fallback for other qa values
      3. <a class="publisherCard-module__info-name___*"> CSS module class
      4. Class-based patterns on any element

    seller_type is derived first from the href path (/inmobiliarias/, etc.),
    then from a type badge in the parent card container.
    """
    name: Optional[str] = None
    seller_type: Optional[str] = None
    seller_link: Optional[Tag] = None

    # 1. data-qa exact matches
    for qa_val in (
        "linkMicrositioAnuncianteLeads",
        "publisher-profile-link",
        "seller-link",
        "anunciante-link",
    ):
        el = soup.find("a", attrs={"data-qa": qa_val})
        if el and isinstance(el, Tag):
            seller_link = el
            break

    # 2. data-qa regex fallback
    if not seller_link:
        el = soup.find("a", attrs={"data-qa": re.compile(r"publisher|anunciante|leads", re.I)})
        if el and isinstance(el, Tag):
            seller_link = el

    # 3. CSS Module class pattern: publisherCard-module__info-name___<hash>
    if not seller_link:
        el = soup.find("a", class_=re.compile(r"publisherCard-module__info-name", re.I))
        if el and isinstance(el, Tag):
            seller_link = el

    if seller_link:
        name = _clean_text(seller_link.get_text(" ", strip=True)) or None
        href = str(seller_link.get("href", ""))

        # Derive type from href path — most reliable, no need for parent card
        seller_type = _seller_type_from_href(href)

        # Fallback: look for type badge in the parent card container
        if not seller_type:
            card = seller_link.find_parent(
                class_=re.compile(r"publisherCard|publisher[-_]card|publisher[-_]module", re.I)
            )
            if card and isinstance(card, Tag):
                type_el = card.find(class_=re.compile(r"type|categoria|label|badge", re.I))
                if type_el and isinstance(type_el, Tag):
                    raw = type_el.get_text(" ", strip=True).lower()
                    if "inmobiliaria" in raw or "empresa" in raw:
                        seller_type = "inmobiliaria"
                    elif "particular" in raw:
                        seller_type = "particular"
                    elif "desarrollador" in raw or "developer" in raw:
                        seller_type = "desarrollador"
    else:
        # 4. Generic class-based fallback
        for pattern in (r"info[-_]?name", r"publisher[-_]?name", r"publisherName"):
            for el in soup.find_all(class_=re.compile(pattern, re.I)):
                if not isinstance(el, Tag):
                    continue
                text = _clean_text(el.get_text(" ", strip=True))
                if text:
                    name = text
                    break
            if name:
                break

    return name, seller_type


def _parse_image_html(soup: BeautifulSoup) -> Optional[str]:
    """Return the main listing image URL."""
    og = soup.find("meta", property="og:image")
    if og and isinstance(og, Tag):
        src = og.get("content", "").strip()
        if src and src.startswith("http"):
            return src
    for container in soup.find_all(class_=re.compile(r"gallery|carousel|photos|slider", re.I)):
        if isinstance(container, Tag):
            for img in container.find_all("img"):
                for attr in ("src", "data-src", "data-lazy", "data-original"):
                    src = img.get(attr, "")
                    if src and str(src).startswith("http"):
                        return str(src)
    return None


def _parse_address_html(soup: BeautifulSoup) -> Optional[str]:
    """Extract a raw address string (fallback when JSON-LD has no location)."""
    for qa in ("posting-location-title", "posting-location", "location-address"):
        el = soup.find(attrs={"data-qa": qa})
        if el and isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            if text:
                return text

    for pattern in (r"location.*address", r"locationProperty", r"section-location"):
        el = soup.find(class_=re.compile(pattern, re.I))
        if el and isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            if text and len(text) < 150:
                return text

    og_title = soup.find("meta", property="og:title")
    if og_title and isinstance(og_title, Tag):
        content = str(og_title.get("content", "")).strip()
        m = re.search(r"\ben\s+(.+?)(?:\s*[-|,]\s*[A-Z]|\s*$)", content)
        if m:
            candidate = m.group(1).strip()
            if candidate and len(candidate) < 120:
                return candidate

    return None


# ── JSON-LD parser ────────────────────────────────────────────────────────────

def _parse_ldjson_property(soup: BeautifulSoup) -> dict:
    """
    Extract structured data from the schema.org JSON-LD block
    (House, Apartment, SingleFamilyResidence, etc.).

    Returns a dict with keys: title, description, direccion, location,
    bedrooms, bathrooms.
    """
    result: dict = {}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("@type") not in (
            "House", "Apartment", "SingleFamilyResidence",
            "Residence", "RealEstateListing",
        ):
            continue

        # title: strip " - Zonaprop" suffix
        name = d.get("name", "")
        if name:
            name = re.sub(r"\s*[-–]\s*Zonaprop\s*$", "", name, flags=re.I).strip()
            # Also strip the city suffix pattern: "Title, City - Zonaprop" → "Title"
            # Keep only the part before the first ", City" if it looks like a suffix
            result["title"] = name

        # description
        desc = d.get("description", "")
        if desc:
            result["description"] = desc.strip()

        # bedrooms / bathrooms from schema.org numeric fields
        if "numberOfBedrooms" in d:
            try:
                result["bedrooms"] = int(d["numberOfBedrooms"])
            except (ValueError, TypeError):
                pass
        if "numberOfBathroomsTotal" in d:
            try:
                result["bathrooms"] = int(d["numberOfBathroomsTotal"])
            except (ValueError, TypeError):
                pass

        # address
        addr = d.get("address") or {}
        if isinstance(addr, dict):
            street = addr.get("streetAddress", "").strip()
            locality = addr.get("addressLocality", "").strip()  # "City, [Region, ]Country, "
            region = addr.get("addressRegion", "").strip()      # neighborhood (barrio)

            if street:
                result["direccion"] = street

            # Parse locality: "Tres de Febrero, GBA Oeste, Argentina, "
            loc_parts = [p.strip() for p in locality.split(",") if p.strip()]
            country: Optional[str] = None
            city: Optional[str] = None
            province: Optional[str] = None

            # Last meaningful part is usually "Argentina"
            if loc_parts and loc_parts[-1].strip().lower() == "argentina":
                country = "Argentina"
                loc_parts = loc_parts[:-1]

            # First remaining part = city
            if loc_parts:
                city = loc_parts[0]

            # Second remaining part = province/region (e.g. "GBA Oeste", "Capital Federal")
            if len(loc_parts) > 1:
                province = loc_parts[1]
            elif city:
                # CABA case: city == province
                province = city

            result["location"] = Location(
                country=country or "Argentina",
                province=province,
                city=city,
                neighborhood=region or None,
                lat=None,
                lon=None,
            )

        break  # first property JSON-LD block is enough

    return result


# ── __NEXT_DATA__ supplement ──────────────────────────────────────────────────

def _find_listing_in_next_data(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    page_props = data.get("props", {}).get("pageProps", {})

    for key in ("listing", "listingData", "posting", "propertyData", "postingData"):
        c = page_props.get(key)
        if isinstance(c, dict) and c:
            return c

    initial = page_props.get("initialData", {})
    if isinstance(initial, dict):
        for key in ("posting", "listing", "propertyData"):
            c = initial.get(key)
            if isinstance(c, dict) and c:
                return c

    return {}


def _extract_price_from_next_data(listing: dict) -> tuple[Optional[str], Optional[int]]:
    """Returns (currency, amount) from __NEXT_DATA__ listing dict."""
    price_obj = listing.get("price") or {}
    if isinstance(price_obj, dict):
        amount = price_obj.get("amount") or price_obj.get("value")
        currency = price_obj.get("currency", "USD")
        if amount:
            try:
                int_amount = int(float(str(amount)))
                if int_amount > 0:
                    return currency, int_amount
            except (ValueError, TypeError):
                pass

    for prices_src in [
        (listing.get("priceOperationType") or {}).get("prices", []),
        listing.get("prices", []),
    ]:
        for p in (prices_src if isinstance(prices_src, list) else []):
            if not isinstance(p, dict):
                continue
            amount = p.get("amount") or p.get("value")
            currency = p.get("currency", "USD")
            if amount:
                try:
                    int_amount = int(float(str(amount)))
                    if int_amount > 0:
                        return currency, int_amount
                except (ValueError, TypeError):
                    pass
    return None, None


# ── Main extractor ────────────────────────────────────────────────────────────

def _build_listing(url: str, html: str) -> PropertyListing:
    """
    Parse a Zonaprop property page HTML and return a PropertyListing.
    Primary sources (in priority order):
      1. JSON-LD schema.org  → title, description, location, bedrooms, bathrooms, direccion
      2. HTML price block    → operation_type, currency, precio
      3. Icon feature list   → areas, ambientes, cochera, antiguedad, orientacion, piso
      4. Inline JS vars      → dias_mercado
      5. __NEXT_DATA__ JSON  → price supplement if HTML parse fails
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD (most reliable for structured location + description)
    ldjson = _parse_ldjson_property(soup)

    # 2. Price block
    price_data = _parse_price_block(soup)

    # 3. Icon features
    icons = _parse_icon_features(soup)

    # 4. Inline JS vars (antiquity)
    script_vars = _parse_inline_script_vars(html)
    days_ago = script_vars.get("published_days_ago")
    if days_ago is None:
        days_ago = _parse_published_days(soup)

    # 5. __NEXT_DATA__ supplement for price
    listing_json = _find_listing_in_next_data(html)
    currency = price_data.get("currency")
    precio = price_data.get("precio")
    if not precio and listing_json:
        currency, precio = _extract_price_from_next_data(listing_json)

    # Property type
    tipo = _parse_property_type(soup)

    # Title: h1 is the cleanest source (no city suffix, correct accents).
    # JSON-LD name is fallback (it has "Title, City - Zonaprop" format).
    h1 = soup.find("h1")
    title = _clean_text(h1.get_text(" ", strip=True)) if h1 else ldjson.get("title")

    # Description fallback: HTML scraping if JSON-LD didn't have it
    description = ldjson.get("description") or _parse_description_html(soup)

    # Seller
    seller_name, seller_type = _parse_seller_html(soup)

    # Image
    imagen_url = _parse_image_html(soup)

    # Address: prefer JSON-LD streetAddress, fallback to HTML
    direccion = ldjson.get("direccion") or _parse_address_html(soup)

    # Cochera / pileta from icons
    cochera = (icons.get("garages", 0) or 0) > 0 or None
    if cochera is False:
        cochera = None  # null = unknown, not "no"

    return PropertyListing(
        url=url,
        portal="zonaprop",
        external_id=_external_id_from_url(url),
        captured_at=datetime.now(_AR_TIMEZONE),
        operation_type=price_data.get("operation_type"),
        currency=currency,
        precio=precio,
        expenses=None,                          # no aparece en el HTML estático
        title=title,
        description=description,
        tipo=tipo,
        ambientes=icons.get("ambiences"),
        bedrooms=ldjson.get("bedrooms") or icons.get("bedrooms"),
        bathrooms=ldjson.get("bathrooms") or icons.get("bathrooms"),
        superficie_total=icons.get("total_area"),
        superficie_cubierta=icons.get("covered_area"),
        superficie_semicubierta=icons.get("uncovered_area"),
        superficie_descubierta=None,
        antiguedad=icons.get("age"),
        orientacion=icons.get("orientation"),
        piso=icons.get("floor"),
        cochera=True if icons.get("garages") else None,
        pileta=None,
        direccion=direccion,
        location=ldjson.get("location"),
        imagen_url=imagen_url,
        dias_mercado=days_ago,
        seller_name=seller_name,
        seller_type=seller_type,
    )


# ── BaseSource ────────────────────────────────────────────────────────────────

class ZonapropSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "zonaprop.com.ar" in url

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        log.info("zonaprop.extract url=%s", url)
        html = await _fetch_html(url, client)
        listing = _build_listing(url, html)
        log.info(
            "zonaprop.extract done url=%s precio=%s location=%s op=%s tipo=%s",
            url,
            listing.precio,
            f"{listing.location.city}/{listing.location.neighborhood}" if listing.location else None,
            listing.operation_type,
            listing.tipo,
        )
        return listing.model_dump(exclude={"url", "portal"})
