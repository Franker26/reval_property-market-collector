"""Zonaprop scraper — full extraction and URL-based search."""
import json
import logging
import re
import unicodedata
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag
from fastapi import HTTPException

from .base import BaseSource
from . import browser as _browser
from .models import (
    ZonapropAddress,
    ZonapropFeatures,
    ZonapropListing,
    ZonapropMedia,
    ZonapropPrice,
    ZonapropSearchRequest,
    ZonapropSeller,
)

log = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

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

# Maps semantic icon class → (field_name, parse_type)
# parse_type: "int" | "float" | "age" | "text"
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

# ── helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower().strip()).strip("-")


async def _fetch_html(url: str, client: httpx.AsyncClient) -> str:
    try:
        r = await client.get(url)
        if r.status_code == 403:
            return await _browser.fetch_rendered(url)
        r.raise_for_status()
        return r.text
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Error al acceder a Zonaprop: {e}")


def _external_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"-(\d{6,})\.html", url)
    return m.group(1) if m else None


def _paginate_url(base_url: str, page: int) -> str:
    if page == 1:
        return base_url
    if base_url.endswith(".html"):
        return base_url[:-5] + f"-pagina-{page}.html"
    return base_url + f"?pagina={page}"


def _ar_number(text: str) -> Optional[float]:
    """Parse Argentine-formatted number: dots=thousands, comma=decimal."""
    # "1.190.000" → 1190000 | "567,5" → 567.5
    cleaned = text.replace(".", "").replace(",", ".")
    m = re.search(r"([\d]+(?:\.\d+)?)", cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _clean_text(text: str) -> str:
    """Collapse whitespace (tabs, newlines, multiple spaces) to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _parse_inline_script_vars(html: str) -> dict:
    """
    Zonaprop embeds JS variables directly in a <script> block inside the
    antiquity/views container.  Extract them from the raw HTML string.

    Variables observed:
      const antiquity   = 'Publicado hace 35 días'
      const usersViews  =  476
    """
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


# ── HTML detail parser ────────────────────────────────────────────────────────

def _parse_icon_features(soup: BeautifulSoup) -> dict:
    """
    Walk every (icon_class → field) in _ICON_FIELD_MAP.
    For each, find the <i class="icon-xxx"> element, climb to its <li> parent,
    extract and parse the text.
    """
    result: dict = {}
    raw_items: list[dict] = []

    for icon_class, field_name, parse_type in _ICON_FIELD_MAP:
        # Try exact class; also handle alternate spellings (e.g. icon-toilet / icon-toilette)
        icon_el = soup.find("i", class_=icon_class)
        if not icon_el and icon_class == "icon-toilette":
            icon_el = soup.find("i", class_="icon-toilet")

        if not icon_el:
            continue

        container: Optional[Tag] = icon_el.find_parent("li") or icon_el.find_parent()
        if not container:
            continue

        # Collapse all whitespace (tabs/newlines from nested elements) to single spaces
        text = _clean_text(container.get_text(" ", strip=True))
        raw_items.append({"icon": icon_class, "field": field_name, "text": text})

        if parse_type == "text":
            result[field_name] = text

        elif parse_type in ("int", "float"):
            val = _ar_number(text)
            if val is not None:
                result[field_name] = int(val) if parse_type == "int" else val

        elif parse_type == "age":
            if re.search(r"estrenar", text, re.I):
                result[field_name] = 0
                result["age_text"] = "A estrenar"
            else:
                m = re.search(r"(\d+)", text)
                if m:
                    result[field_name] = int(m.group(1))
                    # "age_text" should be a clean short string like "36 años"
                    age_m = re.search(r"(\d+\s*años?)", text, re.I)
                    result["age_text"] = age_m.group(1).strip() if age_m else text

    result["_raw_items"] = raw_items
    return result


def _parse_price_block(soup: BeautifulSoup) -> dict:
    """
    Extract operation_type, currency, amount from the price block.

    Observed class variants:
      - class="price-value"       (listing estándar)
      - class="price-items"       (emprendimientos / nueva estructura)
      - class="block-price-container" (contenedor padre, fallback)
    """
    # Orden de prioridad: contenedor más amplio primero para capturar operation_type.
    # price-value (estándar) ya incluye "venta/alquiler + monto".
    # En emprendimientos la estructura es block-price-container > price-operation + price-items,
    # por eso se prefiere el contenedor padre sobre el hijo (price-items) que solo tiene el monto.
    price_div = (
        soup.find(class_=re.compile(r"price-value", re.I))
        or soup.find(class_=re.compile(r"block-price-container", re.I))
        or soup.find(class_=re.compile(r"price-items", re.I))
    )
    if not price_div:
        return {}

    text = price_div.get_text(" ", strip=True)
    result: dict = {"price_text": text}

    # Operation type — appears before the currency symbol
    op_m = re.search(r"^(alquiler\s+temporal|alquiler|venta)", text, re.I)
    if op_m:
        result["operation_type"] = op_m.group(1).strip().lower()

    # Currency + amount
    # Handles: "USD 1.190.000", "$ 450.000", "ARS 100.000"
    price_m = re.search(r"(USD|ARS|\$)\s*([\d.,]+)", text, re.I)
    if price_m:
        sym = price_m.group(1).strip()
        currency = "USD" if sym.upper() == "USD" else "ARS"
        raw_num = price_m.group(2)
        amount = _ar_number(raw_num)
        if amount and amount > 0:
            result["price"] = ZonapropPrice(
                raw_price=f"{sym} {raw_num}",
                currency=currency,
                amount=int(amount),
            )

    return result


def _parse_property_summary(soup: BeautifulSoup) -> dict:
    """
    Extract property_type and property_summary from the property title h2.

    Observed class variants:
      - class="title-type-sup-property"  (listing estándar)
      - class="title-h1-development"     (emprendimientos)
    """
    h2 = (
        soup.find("h2", class_=re.compile(r"title-type-sup-property", re.I))
        or soup.find("h2", class_=re.compile(r"title-h1-development", re.I))
    )
    if not h2:
        return {}

    raw = h2.get_text(" ", strip=True)
    # Normalize various separator characters to " · "
    summary = re.sub(r"\s*[·•|]\s*", " · ", raw).strip()
    parts = [p.strip() for p in re.split(r"\s*[·•|]\s*", raw) if p.strip()]

    result: dict = {"property_summary": summary}
    if parts:
        prop_type_raw = parts[0].lower()
        result["property_type"] = _TIPO_MAP.get(prop_type_raw, parts[0])

    return result


def _parse_advertiser_code(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract advertiser code from patterns like:
      <li><span>Cód. del anunciante:</span> CHO7952270</li>
      <span data-qa="publisher-code">CHO7952270</span>
    """
    # data-qa
    for qa in ("publisher-code", "advertiser-code", "codigo-anunciante"):
        el = soup.find(attrs={"data-qa": qa})
        if el and isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            m = re.search(r"([A-Z]{2,}[A-Z0-9]*)", text)
            if m:
                return m.group(1)

    # li/span with class containing "publisher-codes"
    for el in soup.find_all(class_=re.compile(r"publisher.*code|publisher.*codes|codigo.*anunciante", re.I)):
        if isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            m = re.search(r"Cód\..*?anunciante[:\s]+([A-Z0-9]+)", text, re.I)
            if m:
                return m.group(1).strip()
            # Might just contain the code directly
            m = re.search(r"\b([A-Z]{2,3}\d{5,})\b", text)
            if m:
                return m.group(1)

    # NavigableString containing the label text
    label = soup.find(string=re.compile(r"Cód\..*anunciante|código.*anunciante", re.I))
    if label:
        parent = label.find_parent("li") or label.find_parent()
        if parent and isinstance(parent, Tag):
            full = _clean_text(parent.get_text(" ", strip=True))
            m = re.search(r"Cód\..*?anunciante[:\s]+([A-Z0-9]+)", full, re.I)
            if m:
                return m.group(1).strip()

    # Any li whose full text contains the label
    for li in soup.find_all("li"):
        text = _clean_text(li.get_text(" ", strip=True))
        if not re.search(r"Cód\..*anunciante", text, re.I):
            continue
        m = re.search(r"Cód\..*?anunciante[:\s]+([A-Z0-9]+)", text, re.I)
        if m:
            return m.group(1).strip()

    return None


def _parse_seller(soup: BeautifulSoup) -> Optional[ZonapropSeller]:
    """
    Primary: <a data-qa="linkMicrositioAnuncianteLeads">...</a>
    Fallback chain: data-qa patterns → class patterns → any publisher block.
    """
    name: Optional[str] = None
    profile_url: Optional[str] = None
    image_url: Optional[str] = None
    seller_type: Optional[str] = None

    # ── Try data-qa anchors ────────────────────────────────────────────────────
    seller_link: Optional[Tag] = None
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

    if not seller_link:
        # data-qa regex fallback
        el = soup.find("a", attrs={"data-qa": re.compile(r"publisher|anunciante|leads", re.I)})
        if el and isinstance(el, Tag):
            seller_link = el

    if seller_link:
        name = _clean_text(seller_link.get_text(" ", strip=True)) or None
        href = seller_link.get("href") or ""
        if href:
            profile_url = (
                f"https://www.zonaprop.com.ar{href}" if href.startswith("/") else href
            )

        # Walk up to card container for logo image
        card = seller_link.find_parent(
            class_=re.compile(r"publisherCard|publisher[-_]card|publisher[-_]module", re.I)
        )
        if card and isinstance(card, Tag):
            img = card.find("img")
            if img and isinstance(img, Tag):
                src = img.get("src") or img.get("data-src") or ""
                if src and str(src).startswith("http"):
                    image_url = str(src)

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
        # ── Class-based fallback ───────────────────────────────────────────────
        for pattern in (
            r"info[-_]?name",
            r"publisher[-_]?name",
            r"publisherName",
            r"seller[-_]?name",
            r"anunciante[-_]?nombre",
        ):
            for el in soup.find_all(class_=re.compile(pattern, re.I)):
                if not isinstance(el, Tag):
                    continue
                text = _clean_text(el.get_text(" ", strip=True))
                if text:
                    name = text
                    href = el.get("href") or ""
                    if href:
                        profile_url = (
                            f"https://www.zonaprop.com.ar{href}"
                            if href.startswith("/") else href
                        )
                    break
            if name:
                break

    if not name:
        return None

    return ZonapropSeller(
        name=name,
        type=seller_type,
        image_url=image_url,
        profile_url=profile_url,
        contact_data={},
    )


def _parse_description(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (clean_text, raw_html) from the description block.
    Tries data-qa selectors first, then multiple class patterns.
    """
    desc_el: Optional[Tag] = None

    # data-qa (most stable)
    for qa in ("description", "posting-description", "property-description"):
        el = soup.find(attrs={"data-qa": qa})
        if el and isinstance(el, Tag):
            desc_el = el
            break

    if not desc_el:
        # Class substring patterns — work even with CSS Module hashes
        for pattern in (
            r"wrapper-description",
            r"description-module",
            r"posting.*description",
            r"propertyDescription",
            r"description.*content",
            r"description.*body",
        ):
            el = soup.find(class_=re.compile(pattern, re.I))
            if el and isinstance(el, Tag):
                desc_el = el
                break

    if not desc_el:
        return None, None

    raw_html = str(desc_el)

    for br in desc_el.find_all("br"):
        br.replace_with("\n")

    text = desc_el.get_text("\n", strip=True)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None, raw_html


def _parse_published_at_dom(soup: BeautifulSoup) -> tuple[Optional[str], Optional[int]]:
    """
    DOM-only fallback for published_at.  Skips <script> children to avoid
    picking up the inline JS block that Zonaprop embeds in the same container.
    Only used when _parse_inline_script_vars() finds nothing.
    """
    pub_el: Optional[Tag] = None

    # Try NavigableString first — finds the actual text node, not the script
    nav_str = soup.find(string=re.compile(r"Publicado hace|Publicado hoy|Publicado ayer", re.I))
    if nav_str:
        parent = nav_str.find_parent()
        # Make sure we're NOT inside a script tag
        if parent and parent.name not in ("script", "style"):
            pub_el = parent

    if not pub_el:
        el = soup.find(class_=re.compile(r"post-antiquity|antiquity-views", re.I))
        if el and isinstance(el, Tag) and el.name not in ("script", "style"):
            pub_el = el

    if not pub_el:
        return None, None

    # Remove nested script/style before extracting text
    for child in pub_el.find_all(["script", "style"]):
        child.decompose()

    text = _clean_text(pub_el.get_text(" ", strip=True))
    if not re.search(r"Publicado", text, re.I):
        return None, None

    days: Optional[int] = None
    dm = re.search(r"hace\s+(\d+)\s+días?", text, re.I)
    if dm:
        days = int(dm.group(1))
    elif re.search(r"hoy", text, re.I):
        days = 0
    elif re.search(r"ayer", text, re.I):
        days = 1
    elif re.search(r"hace\s+(\d+)\s+horas?", text, re.I):
        days = 0
    elif re.search(r"hace\s+(\d+)\s+mes", text, re.I):
        n = re.search(r"hace\s+(\d+)\s+mes", text, re.I)
        days = int(n.group(1)) * 30 if n else None

    return text, days


def _parse_views(soup: BeautifulSoup) -> Optional[int]:
    """Look for visible visit/view count in userViews block."""
    # Zonaprop often doesn't expose this; guard against picking up seller name
    views_block = soup.find(class_=re.compile(r"userViews|user-views|post-antiquity", re.I))
    if not views_block:
        return None

    text = views_block.get_text(" ", strip=True) if isinstance(views_block, Tag) else ""
    m = re.search(r"(\d[\d.]*)\s*(?:visitas?|vistas?)", text, re.I)
    if m:
        try:
            return int(m.group(1).replace(".", ""))
        except ValueError:
            pass
    return None


def _parse_media_html(soup: BeautifulSoup) -> ZonapropMedia:
    """Extract gallery from og:image and any <img> inside gallery containers."""
    gallery: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url and url.startswith("http") and url not in seen:
            seen.add(url)
            gallery.append(url)

    # og:image as primary
    og = soup.find("meta", property="og:image")
    if og and isinstance(og, Tag):
        _add(og.get("content", "").strip())

    # Gallery containers
    for container in soup.find_all(class_=re.compile(r"gallery|carousel|photos|slider", re.I)):
        if isinstance(container, Tag):
            for img in container.find_all("img"):
                for attr in ("src", "data-src", "data-lazy", "data-original"):
                    src = img.get(attr, "")
                    if src:
                        _add(str(src))
                        break

    return ZonapropMedia(
        main_image_url=gallery[0] if gallery else None,
        gallery=gallery,
    )


def _parse_address_html(soup: BeautifulSoup, html: str = "") -> Optional[str]:
    """
    Try multiple strategies to extract a raw address string.
    Priority: JSON-LD schema.org > data-qa > location class > og:title > breadcrumb.
    """
    # 1. JSON-LD schema.org — most reliable (always rendered server-side for SEO)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        addr = d.get("address", {})
        if isinstance(addr, dict):
            street = addr.get("streetAddress", "").strip()
            locality = addr.get("addressLocality", "").strip()
            if street:
                return f"{street}, {locality}".strip(", ") if locality else street

    # 2. data-qa attributes (stable across CSS Module hash changes)
    for qa in (
        "posting-location-title",
        "posting-location",
        "breadcrumb-address",
        "location-address",
        "map-address",
    ):
        el = soup.find(attrs={"data-qa": qa})
        if el and isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            if text:
                return text

    # 3. CSS class patterns (stable substring, ignoring CSS Module hash suffix)
    for pattern in (
        r"location.*address",
        r"address.*location",
        r"locationProperty",
        r"location-module.*title",
        r"section-location",
    ):
        el = soup.find(class_=re.compile(pattern, re.I))
        if el and isinstance(el, Tag):
            text = _clean_text(el.get_text(" ", strip=True))
            if text and len(text) < 150:
                return text

    # 4. og:title usually contains "Venta Casa en Husares 2100, Belgrano, ..."
    og_title = soup.find("meta", property="og:title")
    if og_title and isinstance(og_title, Tag):
        content = str(og_title.get("content", "")).strip()
        # Extract the part after " en " (Spanish preposition for location)
        m = re.search(r"\ben\s+(.+?)(?:\s*[-|,]\s*[A-Z]|\s*$)", content)
        if m:
            candidate = m.group(1).strip()
            if candidate and len(candidate) < 120:
                return candidate

    # 5. Breadcrumb nav — last two crumbs (neighborhood + street or just street)
    for nav_el in (
        soup.find("nav", attrs={"aria-label": re.compile(r"breadcrumb", re.I)}),
        soup.find(class_=re.compile(r"breadcrumb", re.I)),
    ):
        if nav_el and isinstance(nav_el, Tag):
            crumbs = [
                _clean_text(li.get_text(" ", strip=True))
                for li in nav_el.find_all("li")
                if _clean_text(li.get_text(" ", strip=True))
            ]
            if len(crumbs) >= 2:
                return ", ".join(crumbs[-2:])
            elif crumbs:
                return crumbs[-1]

    return None


def _parse_html_detail(html: str) -> dict:
    """
    Comprehensive HTML scraper for a Zonaprop property detail page.
    Returns a raw dict consumed by _build_merged_listing().
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {}

    # 1. Icon-based features (most reliable for numeric attrs)
    result["icon_data"] = _parse_icon_features(soup)

    # 2. Price + operation type
    result.update(_parse_price_block(soup))

    # 3. Property type + summary
    result.update(_parse_property_summary(soup))

    # 4. Advertiser code
    code = _parse_advertiser_code(soup)
    if code:
        result["advertiser_code"] = code

    # 5. Seller
    result["seller"] = _parse_seller(soup)

    # 6. Description
    desc_text, desc_html = _parse_description(soup)
    result["description"] = desc_text
    result["description_html"] = desc_html

    # 7. Inline JS variables — primary source for antiquity + usersViews
    #    (the DOM container embeds a <script> block that pollutes get_text())
    script_vars = _parse_inline_script_vars(html)

    if script_vars.get("published_at_text"):
        result["published_at_text"] = script_vars["published_at_text"]
        result["published_days_ago"] = script_vars.get("published_days_ago")
    else:
        # Fall back to DOM parsing (skips script children)
        pub_text, pub_days = _parse_published_at_dom(soup)
        result["published_at_text"] = pub_text
        result["published_days_ago"] = pub_days

    # views_count: inline JS is authoritative; DOM fallback only if not found
    result["views_count"] = script_vars.get("views_count") or _parse_views(soup)

    # 8. Media
    result["media"] = _parse_media_html(soup)

    # 9. Raw address (used when __NEXT_DATA__ has no location)
    result["raw_address_html"] = _parse_address_html(soup, html)

    return result


# ── __NEXT_DATA__ parsers ─────────────────────────────────────────────────────

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

    dehydrated = page_props.get("dehydratedState", {})
    if isinstance(dehydrated, dict):
        for q in dehydrated.get("queries", []):
            d = q.get("state", {}).get("data", {})
            if isinstance(d, dict) and ("price" in d or "location" in d or "photos" in d):
                return d

    return {}


def _extract_price_json(listing: dict) -> Optional[ZonapropPrice]:
    """Pull price from __NEXT_DATA__ listing dict."""
    price_obj = listing.get("price") or {}
    if isinstance(price_obj, dict):
        amount = price_obj.get("amount") or price_obj.get("value")
        currency = price_obj.get("currency", "USD")
        if amount:
            try:
                int_amount = int(float(str(amount)))
                if int_amount > 0:
                    return ZonapropPrice(
                        raw_price=f"{currency} {int_amount:,}",
                        currency=currency,
                        amount=int_amount,
                    )
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
                        return ZonapropPrice(
                            raw_price=f"{currency} {int_amount:,}",
                            currency=currency,
                            amount=int_amount,
                        )
                except (ValueError, TypeError):
                    pass
    return None


def _extract_address_json(listing: dict) -> ZonapropAddress:
    """Pull structured address/location from __NEXT_DATA__ listing dict."""
    raw: Optional[str] = None
    for getter in [
        lambda l: l.get("address") if isinstance(l.get("address"), str) else None,
        lambda l: (l.get("location") or {}).get("address", {}).get("name")
            if isinstance((l.get("location") or {}).get("address"), dict) else None,
        lambda l: (l.get("location") or {}).get("fullLocation"),
    ]:
        try:
            v = getter(listing)
            if isinstance(v, str) and v.strip():
                raw = v.strip()
                break
        except Exception:
            pass

    neighborhood = city = province = None
    location = listing.get("location") or {}
    if isinstance(location, dict):
        nb = location.get("neighborhood") or {}
        if isinstance(nb, dict):
            neighborhood = nb.get("name") or nb.get("label")
        elif isinstance(nb, str):
            neighborhood = nb

        for ck in ("city", "zone", "municipality"):
            cv = location.get(ck) or {}
            if isinstance(cv, dict):
                city = cv.get("name") or cv.get("label")
            elif isinstance(cv, str):
                city = cv
            if city:
                break

        state = location.get("state") or location.get("province") or {}
        if isinstance(state, dict):
            province = state.get("name") or state.get("label")
        elif isinstance(state, str):
            province = state

    parts = [p for p in [raw, neighborhood, city, province] if p]
    standardized = ", ".join(dict.fromkeys(parts)) if parts else None

    return ZonapropAddress(
        raw_address=raw,
        standardized_address=standardized,
        neighborhood=neighborhood,
        city=city,
        province=province,
    )


def _extract_media_json(listing: dict) -> ZonapropMedia:
    gallery: list[str] = []
    for key in ("photos", "pictures", "images", "gallery"):
        items = listing.get(key) or []
        if not isinstance(items, list) or not items:
            continue
        for item in items:
            if isinstance(item, dict):
                img = item.get("url") or item.get("src") or item.get("image") or item.get("originalUrl")
            elif isinstance(item, str):
                img = item
            else:
                continue
            if isinstance(img, str) and img.startswith("http"):
                gallery.append(img)
        if gallery:
            break
    return ZonapropMedia(
        main_image_url=gallery[0] if gallery else None,
        gallery=gallery,
    )


# ── Search URL builder ────────────────────────────────────────────────────────

def _build_search_url(req: ZonapropSearchRequest) -> str:
    """Compose a Zonaprop search URL from structured parameters."""
    path = f"{_slugify(req.tipo)}s-{req.operacion}-{_slugify(req.ubicacion)}"

    if req.precio_min or req.precio_max:
        lo = req.precio_min or 0
        hi = req.precio_max or 999_999_999
        path += f"-{lo}-{hi}-dolar"

    return f"https://www.zonaprop.com.ar/{path}.html"


# ── Merge both sources into final listing ─────────────────────────────────────

def _build_merged_listing(
    url: str,
    html_data: dict,
    listing_json: dict,
    raw_html: str,
) -> ZonapropListing:
    """
    Combine HTML-parsed data (primary for UI fields) with __NEXT_DATA__ JSON
    (supplement for structured location and gallery).
    """
    icon = html_data.get("icon_data", {})
    raw_items = icon.pop("_raw_items", [])

    # ── Price ──────────────────────────────────────────────────────────────────
    price: Optional[ZonapropPrice] = html_data.get("price")
    if not (price and price.amount) and listing_json:
        price = _extract_price_json(listing_json)

    # ── Address ────────────────────────────────────────────────────────────────
    address: Optional[ZonapropAddress] = None
    if listing_json:
        addr_json = _extract_address_json(listing_json)
        if addr_json.raw_address or addr_json.neighborhood:
            address = addr_json

    if not address or not address.raw_address:
        # Supplement raw_address from HTML
        raw_addr_html = html_data.get("raw_address_html")
        if raw_addr_html:
            if address:
                address = address.model_copy(update={"raw_address": raw_addr_html})
            else:
                address = ZonapropAddress(raw_address=raw_addr_html, standardized_address=raw_addr_html)

    # ── Features (icon-based HTML is primary) ──────────────────────────────────
    ambiences: Optional[int] = icon.get("ambiences")
    features = ZonapropFeatures(
        ambiences=ambiences,
        rooms=ambiences,                       # alias
        bedrooms=icon.get("bedrooms"),
        bathrooms=icon.get("bathrooms"),
        toilettes=icon.get("toilettes"),
        garages=icon.get("garages"),
        total_area=icon.get("total_area"),
        covered_area=icon.get("covered_area"),
        uncovered_area=icon.get("uncovered_area"),
        age=icon.get("age"),
        orientation=icon.get("orientation"),
        floor=icon.get("floor"),
        amenities=[],
    )

    # ── Media ──────────────────────────────────────────────────────────────────
    media: ZonapropMedia = html_data.get("media") or ZonapropMedia()
    if not media.gallery and listing_json:
        media = _extract_media_json(listing_json)

    # ── Seller ─────────────────────────────────────────────────────────────────
    seller: Optional[ZonapropSeller] = html_data.get("seller")

    # ── Operation / property type ──────────────────────────────────────────────
    operation_type: Optional[str] = html_data.get("operation_type")
    if not operation_type and listing_json:
        for key in ("operationType", "operation", "listingType"):
            op = listing_json.get(key) or {}
            name = (op.get("name") or op.get("label") or "") if isinstance(op, dict) else str(op)
            if name:
                operation_type = name.lower()
                break

    property_type: Optional[str] = html_data.get("property_type")
    if not property_type and listing_json:
        for key in ("type", "propertyType"):
            pt = listing_json.get(key) or {}
            name = (pt.get("name") or pt.get("label") or "") if isinstance(pt, dict) else str(pt)
            if name:
                property_type = _TIPO_MAP.get(name.lower(), name)
                break

    # ── Title (JSON only — not always present) ─────────────────────────────────
    title: Optional[str] = None
    if listing_json:
        raw_title = listing_json.get("title") or listing_json.get("name")
        title = str(raw_title).strip() if raw_title else None

    return ZonapropListing(
        source="zonaprop",
        external_id=_external_id_from_url(url),
        advertiser_code=html_data.get("advertiser_code"),
        url=url,
        operation_type=operation_type,
        property_type=property_type,
        title=title,
        price=price if (price and price.amount) else None,
        address=address if (address and address.raw_address) else None,
        features=features,
        media=media,
        seller=seller,
        description=html_data.get("description"),
        published_days_ago=html_data.get("published_days_ago"),
        views_count=html_data.get("views_count"),
    )


# ── Search URL helpers ────────────────────────────────────────────────────────

def _extract_urls_from_preloaded_state(html: str) -> list[str]:
    m = re.search(
        r"window\.__PRELOADED_STATE__\s*=\s*(\{.+?\});\s*(?:</script>|window\.)",
        html,
        re.DOTALL,
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    postings = data.get("listStore", {}).get("listPostings", [])
    urls: list[str] = []
    for p in postings:
        url = p.get("url") or p.get("postingUrl") or p.get("permalink")
        if isinstance(url, str) and url:
            if url.startswith("/"):
                url = f"https://www.zonaprop.com.ar{url}"
            urls.append(url)
    return urls


def _extract_urls_from_next_data(html: str) -> list[str]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    page_props = data.get("props", {}).get("pageProps", {})
    postings = (
        page_props.get("listPostings")
        or page_props.get("listings")
        or (page_props.get("initialData") or {}).get("listPostings")
        or []
    )
    if not isinstance(postings, list):
        return []

    urls: list[str] = []
    for p in postings:
        if not isinstance(p, dict):
            continue
        url = p.get("url") or p.get("postingUrl") or p.get("permalink")
        if isinstance(url, str) and url:
            if url.startswith("/"):
                url = f"https://www.zonaprop.com.ar{url}"
            urls.append(url)
    return urls


def _extract_search_urls(html: str) -> list[str]:
    urls = _extract_urls_from_preloaded_state(html)
    if not urls:
        urls = _extract_urls_from_next_data(html)
    if not urls:
        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()
        for a in soup.find_all("a", href=re.compile(r"/propiedades/.+\.html")):
            href = a["href"].split("?")[0]
            if not href.startswith("http"):
                href = f"https://www.zonaprop.com.ar{href}"
            if href not in seen:
                seen.add(href)
                urls.append(href)
    return urls


# ── Public API ────────────────────────────────────────────────────────────────

async def extract_full(url: str, client: httpx.AsyncClient) -> ZonapropListing:
    """Fetch a Zonaprop property page and return a fully-populated ZonapropListing."""
    log.info("zonaprop.extract_full url=%s", url)
    html = await _fetch_html(url, client)

    # Run both parsers; HTML is primary for UI-visible fields
    html_data = _parse_html_detail(html)
    listing_json = _find_listing_in_next_data(html)

    result = _build_merged_listing(url, html_data, listing_json, html)

    log.info(
        "zonaprop.extract_full done url=%s price=%s address=%s op=%s type=%s",
        url,
        result.price.amount if result.price else None,
        result.address.raw_address if result.address else None,
        result.operation_type,
        result.property_type,
    )
    return result


async def search_by_url(
    search_url: str,
    max_pages: int,
    client: httpx.AsyncClient,
) -> list[ZonapropListing]:
    """
    Crawl a Zonaprop search URL, paginate, extract each listing.
    Discards listings missing price or address.
    """
    listing_urls: list[str] = []

    for page in range(1, max_pages + 1):
        page_url = _paginate_url(search_url, page)
        log.info("zonaprop.search page=%d url=%s", page, page_url)
        try:
            html = await _fetch_html(page_url, client)
        except HTTPException as e:
            log.warning("zonaprop.search page=%d failed: %s", page, e.detail)
            break

        found = _extract_search_urls(html)
        log.info("zonaprop.search page=%d found %d listing URLs", page, len(found))
        listing_urls.extend(found)

        if not found:
            break

    listing_urls = list(dict.fromkeys(listing_urls))

    results: list[ZonapropListing] = []
    discarded = 0

    for lurl in listing_urls:
        try:
            item = await extract_full(lurl, client)
        except HTTPException as e:
            log.warning("zonaprop.search extract failed url=%s: %s", lurl, e.detail)
            continue

        if not item.price or not item.price.amount:
            log.info("zonaprop.search discarded (no price) url=%s", lurl)
            discarded += 1
            continue

        if not item.address or not item.address.raw_address:
            log.info("zonaprop.search discarded (no address) url=%s", lurl)
            discarded += 1
            continue

        results.append(item)

    log.info(
        "zonaprop.search done total=%d accepted=%d discarded=%d",
        len(listing_urls), len(results), discarded,
    )
    return results


async def search_by_params(
    req: ZonapropSearchRequest,
    client: httpx.AsyncClient,
) -> list[ZonapropListing]:
    """Build the Zonaprop search URL from structured params, then filter by superficie."""
    search_url = _build_search_url(req)
    log.info("zonaprop.search_by_params url=%s", search_url)
    listings = await search_by_url(search_url, req.max_pages, client)

    if req.superficie_min or req.superficie_max:
        filtered = []
        for item in listings:
            area = item.features.total_area or item.features.covered_area
            if area is None:
                filtered.append(item)  # no descartamos si no tenemos el dato
                continue
            if req.superficie_min and area < req.superficie_min:
                continue
            if req.superficie_max and area > req.superficie_max:
                continue
            filtered.append(item)
        log.info(
            "zonaprop.search_by_params superficie filter: %d → %d",
            len(listings), len(filtered),
        )
        listings = filtered

    return listings


# ── BaseSource compatibility (generic /extract and /search endpoints) ─────────

class ZonapropSource(BaseSource):
    @staticmethod
    def can_handle(url: str) -> bool:
        return "zonaprop.com.ar" in url

    async def extract(self, url: str, client: httpx.AsyncClient) -> dict:
        """Flat dict for the generic /extract endpoint (PropertyListing compat)."""
        listing = await extract_full(url, client)
        result: dict = {}

        if listing.price and listing.price.amount:
            result["precio"] = listing.price.amount

        if listing.address and listing.address.raw_address:
            result["direccion"] = listing.address.raw_address

        if listing.media.main_image_url:
            result["imagen_url"] = listing.media.main_image_url

        if listing.property_type:
            result["tipo"] = listing.property_type

        if listing.features.ambiences is not None:
            result["ambientes"] = listing.features.ambiences

        if listing.features.total_area is not None:
            result["superficie_total"] = listing.features.total_area

        if listing.features.covered_area is not None:
            result["superficie_cubierta"] = listing.features.covered_area

        if listing.features.uncovered_area is not None:
            result["superficie_semicubierta"] = listing.features.uncovered_area

        if listing.features.age is not None:
            result["antiguedad"] = listing.features.age

        if listing.features.orientation:
            result["orientacion"] = listing.features.orientation

        if listing.features.floor is not None:
            result["piso"] = listing.features.floor

        if listing.features.garages:
            result["cochera"] = listing.features.garages > 0

        amenities_lower = {a.lower() for a in listing.features.amenities}
        if any(w in amenities_lower for w in ("pileta", "piscina", "pool")):
            result["pileta"] = True

        if listing.published_days_ago is not None:
            result["dias_mercado"] = listing.published_days_ago

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
        slug_tipo = _slugify(tipo)
        slug_ubi = _slugify(ubicacion)
        urls: list[str] = []

        for page in range(1, paginas + 1):
            path = f"{slug_tipo}s-{operacion}-{slug_ubi}"

            if ambientes_min and ambientes_min == ambientes_max:
                path += f"-{ambientes_min}-ambientes"
            elif ambientes_min:
                path += f"-{ambientes_min}-ambientes"

            if precio_min or precio_max:
                lo = precio_min or 0
                hi = precio_max or 999_999_999
                path += f"-{lo}-{hi}-dolar"

            search_url = f"https://www.zonaprop.com.ar/{path}.html?pagina={page}"

            try:
                html = await _fetch_html(search_url, client)
            except HTTPException:
                continue

            page_urls = _extract_search_urls(html)
            urls.extend(page_urls)

        return urls
