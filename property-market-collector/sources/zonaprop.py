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
from .models import (
    ListingInfo, LocationInfo, MediaInfo,
    PriceInfo, PropertyInfo, PropertyListing, SellerInfo,
)

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

_TIER_MAP = {"1": "simple", "2": "destacado", "3": "superdestacado", "4": "free"}


def _normalize_coord(value, *, is_lon: bool = False) -> Optional[float]:
    """
    Convert a raw coordinate value (float or int64) to decimal degrees.
    Zonaprop stores coordinates as integers (e.g. -34603722 → -34.603722).
    Tries divisors 10^7, 10^6, 10^5 until the result falls in a valid range.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    max_abs = 180.0 if is_lon else 90.0
    if abs(v) <= max_abs:
        return round(v, 7)
    for exp in (7, 6, 5):
        candidate = v / (10 ** exp)
        if abs(candidate) <= max_abs:
            return round(candidate, 7)
    return None


def _parse_inline_script_vars(html: str) -> dict:
    """Extract all inline JS vars from Zonaprop's server-rendered script blocks."""
    result: dict = {}

    # ── Antiquity ─────────────────────────────────────────────────────────────
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

    # ── Posting type / status ─────────────────────────────────────────────────
    m = re.search(r'postingType\s*=\s*"([^"]+)"', html)
    if m and m.group(1):
        result["posting_type"] = m.group(1)

    m = re.search(r'postingStatus\s*=\s*"([^"]+)"', html)
    if m:
        result["posting_status"] = m.group(1)

    # ── Publication tier ──────────────────────────────────────────────────────
    # The tier map {'1':'simple','2':'destacado',...} is always in the HTML;
    # we look for the variable that indexes it in the preceding context.
    tier_map_m = re.search(r"'1'\s*:\s*'simple'", html)
    if tier_map_m:
        ctx = html[max(0, tier_map_m.start() - 400):tier_map_m.start()]
        tier_m = re.search(r"tier\s*[=:]\s*[\"']?(\d)[\"']?", ctx)
        if tier_m:
            result["publication_tier"] = _TIER_MAP.get(tier_m.group(1))

    # ── Publisher object ──────────────────────────────────────────────────────
    pub_m = re.search(r'publisher\s*=\s*(\{[^\n]+\})', html)
    if pub_m:
        try:
            pub = json.loads(pub_m.group(1))
            result["seller_id"] = str(pub["publisherId"]) if pub.get("publisherId") else None
            result["seller_license"] = pub.get("license") or None
            result["seller_logo_url"] = pub.get("urlLogo") or None
        except Exception:
            pass

    # ── Address visibility ────────────────────────────────────────────────────
    vis_m = re.search(r'"visibility"\s*:\s*"(EXACT|APPROXIMATE|HIDDEN)"', html, re.I)
    if vis_m:
        result["address_visibility"] = vis_m.group(1).upper()

    # ── Video / tour 360 ─────────────────────────────────────────────────────
    result["has_video"] = bool(re.search(r'"videoUrl"\s*:\s*"https[^"]+"|const videos\s*=\s*\[.+\]', html))
    result["has_tour_360"] = bool(re.search(r'tour360Url\s*=\s*"https[^"]+"|"tourUrl"\s*:\s*"https[^"]+"', html))

    # ── Coordinates (Zonaprop stores as int64, e.g. -34603722 = -34.603722°) ─
    lat_m = re.search(r'\blatitude\s*[=:]\s*["\']?(-?\d+(?:\.\d+)?)["\']?', html, re.I)
    lon_m = re.search(r'\blongitude\s*[=:]\s*["\']?(-?\d+(?:\.\d+)?)["\']?', html, re.I)
    if lat_m:
        lat = _normalize_coord(lat_m.group(1), is_lon=False)
        if lat is not None:
            result["lat"] = lat
    if lon_m:
        lon = _normalize_coord(lon_m.group(1), is_lon=True)
        if lon is not None:
            result["lon"] = lon

    return result


def _parse_pictures_count(html: str, external_id: Optional[str]) -> Optional[int]:
    """
    Count unique gallery images by matching imgar CDN paths for this posting.
    The CDN path encodes the posting ID: avisos/1/00/XX/XX/XX/XX/<size>/<image_id>.jpg
    We extract unique <image_id> values to get the real picture count.
    """
    if not external_id or len(external_id) < 6:
        return None

    # Build path segments from the external_id digits, grouped in pairs
    digits = external_id.zfill(8)
    path_segments = "/".join([digits[i:i+2] for i in range(0, 8, 2)])
    pattern = rf"avisos/\d+/{path_segments}/[^/]+/(\d+)\.jpg"

    image_ids = set(re.findall(pattern, html))
    return len(image_ids) if image_ids else None


def _parse_general_features(html: str) -> dict:
    """
    Parse the generalFeatures JS object into a flat features dict.

    Structure: { "Section name": { featureId: { label, value, measure, icon } } }

    Rules:
      - value=None  → feature is present (boolean True)
      - value="0"   → skip (explicitly absent)
      - value=text  → include as string
      - value=num   → include as number
    Skip structural/area features already captured in dedicated fields
    (superficies, ambientes, dormitorios, baños, antigüedad, orientación).
    """
    SKIP_ICONS = {"stotal", "scubierta", "ssemi", "ambiente", "bano", "dormitorio",
                  "cochera", "antiguedad", "orientacion", "piso", "toilette", "garages"}

    m = re.search(r"(?:const\s+)?generalFeatures\s*=\s*(\{)", html)
    if not m:
        return {}

    start = m.start(1)
    depth, end = 0, start
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        sections = json.loads(html[start:end])
    except Exception:
        return {}

    features: dict = {}
    for section_features in sections.values():
        if not isinstance(section_features, dict):
            continue
        for feat in section_features.values():
            if not isinstance(feat, dict):
                continue
            icon = feat.get("icon") or ""
            if icon in SKIP_ICONS:
                continue
            label = feat.get("label", "").strip()
            if not label:
                continue
            value = feat.get("value")
            measure = feat.get("measure") or ""

            # Normalise label to snake_case key
            key = re.sub(r"\s+", "_", label.lower().strip())
            key = re.sub(r"[^a-z0-9_áéíóúñü]", "", key)
            key = re.sub(r"_+", "_", key).strip("_")

            if value is None:
                features[key] = True
            elif str(value) == "0":
                continue  # explicitly absent
            else:
                # Try numeric
                try:
                    num = float(str(value).replace(",", ".").replace(".", "", str(value).count(".") - 1))
                    features[key] = int(num) if num == int(num) else num
                except (ValueError, OverflowError):
                    features[key] = f"{value} {measure}".strip() if measure else str(value)

    return features


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

            if loc_parts and loc_parts[-1].strip().lower() == "argentina":
                country = "Argentina"
                loc_parts = loc_parts[:-1]

            if loc_parts:
                city = loc_parts[0]

            if len(loc_parts) > 1:
                province = loc_parts[1]
            elif city:
                province = city  # CABA: city == province

            result["country"] = country or "Argentina"
            result["province"] = province
            result["city"] = city
            result["neighborhood"] = region or None

        # coordinates from schema.org geo
        geo = d.get("geo") or {}
        if isinstance(geo, dict):
            lat = _normalize_coord(geo.get("latitude"), is_lon=False)
            lon = _normalize_coord(geo.get("longitude"), is_lon=True)
            if lat is not None:
                result["lat"] = lat
            if lon is not None:
                result["lon"] = lon

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


def _extract_coords_from_next_data(listing: dict) -> tuple[Optional[float], Optional[float]]:
    """
    Extract lat/lon from __NEXT_DATA__ listing dict.
    Handles both float degrees and int64 formats.
    Returns (lat, lon) or (None, None).
    """
    for geo_key in ("geo", "location", "address", "coordinates", "geoLocation"):
        geo = listing.get(geo_key)
        if not isinstance(geo, dict):
            continue
        raw_lat = geo.get("lat") or geo.get("latitude")
        raw_lon = geo.get("lon") or geo.get("longitude")
        if raw_lat is not None and raw_lon is not None:
            lat = _normalize_coord(raw_lat, is_lon=False)
            lon = _normalize_coord(raw_lon, is_lon=True)
            if lat is not None and lon is not None:
                return lat, lon
    return None, None


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
    Priority order per data point:
      1. JSON-LD schema.org  → title, description, location (address + geo), bedrooms, bathrooms
      2. HTML price block    → operation_type, currency, precio
      3. Icon feature list   → surfaces, ambientes, cochera, antiguedad, orientacion, piso
      4. Inline JS vars      → dias_mercado, posting metadata, seller ids, coords (int64), media flags
      5. __NEXT_DATA__ JSON  → price & coords supplement if previous sources fail
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. JSON-LD
    ldjson = _parse_ldjson_property(soup)

    # 2. Price block
    price_data = _parse_price_block(soup)

    # 3. Icon features
    icons = _parse_icon_features(soup)

    # 4. Inline JS vars (all metadata extracted in one pass)
    script_vars = _parse_inline_script_vars(html)
    days_ago = script_vars.get("published_days_ago")
    if days_ago is None:
        days_ago = _parse_published_days(soup)

    # 5. General features (amenities dict)
    features = _parse_general_features(html)

    # 6. Picture count: calculated from CDN URL patterns, not from API
    ext_id = _external_id_from_url(url)
    pictures_count = _parse_pictures_count(html, ext_id)

    # 7. __NEXT_DATA__: supplement price and coords if missing
    listing_json = _find_listing_in_next_data(html)
    currency = price_data.get("currency")
    precio = price_data.get("precio")
    if not precio and listing_json:
        currency, precio = _extract_price_from_next_data(listing_json)

    # ── Coordinates: JSON-LD geo > inline JS int64 > __NEXT_DATA__ ────────────
    lat = ldjson.get("lat") or script_vars.get("lat")
    lon = ldjson.get("lon") or script_vars.get("lon")
    if (lat is None or lon is None) and listing_json:
        nd_lat, nd_lon = _extract_coords_from_next_data(listing_json)
        lat = lat or nd_lat
        lon = lon or nd_lon

    # ── Title ────────────────────────────────────────────────────────────────
    # h1 is cleanest (no city suffix). JSON-LD name is fallback.
    h1 = soup.find("h1")
    title = _clean_text(h1.get_text(" ", strip=True)) if h1 else ldjson.get("title")

    # ── Description ──────────────────────────────────────────────────────────
    description = ldjson.get("description") or _parse_description_html(soup)

    # ── Seller ────────────────────────────────────────────────────────────────
    seller_name, seller_type = _parse_seller_html(soup)

    # ── Address ───────────────────────────────────────────────────────────────
    direccion = ldjson.get("direccion") or _parse_address_html(soup)

    # ── Assemble sub-models ───────────────────────────────────────────────────
    return PropertyListing(
        url=url,
        portal="zonaprop",
        external_id=ext_id,
        captured_at=datetime.now(_AR_TIMEZONE),
        listing=ListingInfo(
            title=title,
            description=description,
            operation_type=price_data.get("operation_type"),
            dias_mercado=days_ago,
            posting_type=script_vars.get("posting_type"),
            posting_status=script_vars.get("posting_status"),
            publication_tier=script_vars.get("publication_tier"),
        ),
        price=PriceInfo(
            currency=currency,
            precio=precio,
            expenses=None,  # no aparece en el HTML estático
        ),
        property_info=PropertyInfo(
            tipo=_parse_property_type(soup),
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
            features=features,
        ),
        location=LocationInfo(
            direccion=direccion,
            country=ldjson.get("country"),
            province=ldjson.get("province"),
            city=ldjson.get("city"),
            neighborhood=ldjson.get("neighborhood"),
            lat=lat,
            lon=lon,
            address_visibility=script_vars.get("address_visibility"),
        ),
        media=MediaInfo(
            imagen_url=_parse_image_html(soup),
            pictures_count=pictures_count,
            has_video=script_vars.get("has_video"),
            has_tour_360=script_vars.get("has_tour_360"),
        ),
        seller=SellerInfo(
            name=seller_name,
            type=seller_type,
            id=script_vars.get("seller_id"),
            license=script_vars.get("seller_license"),
            logo_url=script_vars.get("seller_logo_url"),
        ),
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
        return listing.model_dump(by_alias=True, exclude={"url", "portal"})
