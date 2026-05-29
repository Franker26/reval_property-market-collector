"""
discovery.zonaprop.api_postings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Discovery de publicaciones de Zonaprop vía API interna POST /rplis-api/postings.

Usa Playwright para el warmup (resolver Cloudflare) y para las requests de API,
ya que httpx es bloqueado por el TLS fingerprint aunque tenga cf_clearance.

Playwright's context.request hace HTTP real sin renderizar JS — es liviano.

Paginación confirmada via DevTools: parámetro `pagina` (entero, base 1).
Respuesta confirmada: `listPostings`, `paging.total`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, Optional

from app.core.config import get_settings
from app.core.rate_limiter import CooldownError, get_rate_limiter

log = logging.getLogger(__name__)

_AR_TZ = timezone(timedelta(hours=-3))
_PAGE_SIZE = 30


# ── Payload ───────────────────────────────────────────────────────────────────


def build_payload(page: int, tipo_operacion: str = "1") -> dict:
    """
    Payload exacto confirmado via DevTools del browser.
    pagina: número de página base 1.
    tipoDeOperacion: "1" = venta, "2" = alquiler.
    """
    return {
        "q": None,
        "direccion": None,
        "moneda": "",
        "preciomin": None,
        "preciomax": None,
        "services": "",
        "general": "",
        "searchbykeyword": "",
        "amenidades": "",
        "caracteristicasprop": None,
        "comodidades": "",
        "disposicion": None,
        "roomType": "",
        "outside": "",
        "areaPrivativa": "",
        "areaComun": "",
        "multipleRets": "",
        "tipoDePropiedad": "",
        "subtipoDePropiedad": None,
        "tipoDeOperacion": tipo_operacion,
        "garages": None,
        "antiguedad": None,
        "expensasminimo": None,
        "expensasmaximo": None,
        "withoutguarantor": None,
        "habitacionesminimo": 0,
        "habitacionesmaximo": 0,
        "ambientesminimo": 0,
        "ambientesmaximo": 0,
        "banos": None,
        "superficieCubierta": 1,
        "idunidaddemedida": 1,
        "metroscuadradomin": None,
        "metroscuadradomax": None,
        "tipoAnunciante": "ALL",
        "grupoTipoDeMultimedia": "",
        "publicacion": None,
        "sort": "relevance",
        "etapaDeDesarrollo": "",
        "auctions": None,
        "polygonApplied": None,
        "idInmobiliaria": None,
        "excludePostingContacted": "",
        "banks": "",
        "places": "",
        "condominio": "",
        "preTipoDeOperacion": "",
        "pagina": page,
        "city": None,
        "province": None,
        "zone": None,
        "valueZone": None,
        "subZone": None,
        "coordenates": None,
    }


# ── Parsers de respuesta ──────────────────────────────────────────────────────


def _extract_postings(data: dict) -> list[dict]:
    """Zonaprop usa listPostings."""
    for key in ("listPostings", "listObjects", "postings", "items", "results", "data"):
        items = data.get(key)
        if isinstance(items, list) and items:
            return items
    return []


def _extract_total(data: dict) -> Optional[int]:
    """Zonaprop: paging.total (int) o totalPosting (string '604.824')."""
    paging = data.get("paging")
    if isinstance(paging, dict):
        val = paging.get("total")
        if isinstance(val, int):
            return val
    raw = data.get("totalPosting") or data.get("totalPostings")
    if raw is not None:
        try:
            return int(str(raw).replace(".", "").replace(",", ""))
        except ValueError:
            pass
    for key in ("totalCount", "total", "count"):
        val = data.get(key)
        if isinstance(val, int):
            return val
    return None


def _parse_posting(raw: dict, base_url: str) -> Optional[dict]:
    """Normaliza un posting de listPostings."""
    external_id = str(raw.get("postingId") or raw.get("id") or "").strip()
    if not external_id:
        return None

    raw_url = raw.get("url") or raw.get("link") or ""
    if raw_url.startswith("/"):
        canonical_url = base_url.rstrip("/") + raw_url
    elif raw_url.startswith("http"):
        canonical_url = raw_url
    else:
        canonical_url = f"{base_url}/propiedades/{external_id}.html"

    op_raw = raw.get("operationType") or raw.get("operation") or {}
    operation_type = (op_raw.get("name") or "").lower() if isinstance(op_raw, dict) else None

    type_raw = raw.get("propertyType") or raw.get("realestateType") or raw.get("type") or {}
    property_type = (type_raw.get("name") or "").lower() if isinstance(type_raw, dict) else None

    return {
        "external_id": external_id,
        "canonical_url": canonical_url,
        "operation_type": operation_type or None,
        "property_type": property_type or None,
    }


# ── Discovery loop ────────────────────────────────────────────────────────────


async def discover(
    tipo_operacion: str = "1",
    max_pages: int = 50,
    persist_fn: Optional[Callable[[list[dict], int], Awaitable[None]]] = None,
) -> dict:
    """
    Recorre páginas de la API de Zonaprop y descubre publicaciones.

    Args:
        tipo_operacion: "1" = venta, "2" = alquiler.
        max_pages: límite de páginas para esta corrida.
        persist_fn: corrutina async(postings, page_num) para persistir cada página.

    Returns:
        dict con stats: pages_ok, pages_failed, total_found, total_reported.
    """
    from sources.browser import get_browser
    from sources.session_manager import create_zonaprop_session

    settings = get_settings()
    rate = get_rate_limiter("zonaprop_api")

    stats: dict = {
        "pages_ok": 0,
        "pages_failed": 0,
        "total_found": 0,
        "total_reported": None,
        "stopped_early": False,
    }

    browser = await get_browser()
    session = await create_zonaprop_session(
        warmup_url=settings.zonaprop_warmup_url,
        browser=browser,
        user_agent=settings.zonaprop_user_agent,
        base_url=settings.zonaprop_base_url,
    )

    try:
        if not session.cf_clearance:
            log.warning("api_postings: sin cf_clearance — la IP puede estar bloqueada en Cloudflare")

        await asyncio.sleep(2)

        for page_num in range(1, max_pages + 1):
            try:
                await rate.wait()
            except CooldownError as exc:
                log.error("api_postings: cooldown activo — %s", exc)
                stats["stopped_early"] = True
                break

            payload = build_payload(page=page_num, tipo_operacion=tipo_operacion)
            data = await session.post_json(settings.zonaprop_api_postings_url, payload)

            if data is None:
                rate.record_error()
                stats["pages_failed"] += 1
                continue

            if "__http_error__" in data:
                status = data["__http_error__"]
                rate.record_error(http_status=status)
                stats["pages_failed"] += 1
                if status in (403, 429):
                    log.error("api_postings: HTTP %d — deteniendo", status)
                    stats["stopped_early"] = True
                    break
                continue

            rate.record_success()

            postings_raw = _extract_postings(data)
            if not postings_raw:
                log.info("api_postings: página %d vacía — fin del catálogo", page_num)
                break

            if stats["total_reported"] is None:
                stats["total_reported"] = _extract_total(data)
                if stats["total_reported"]:
                    log.info("api_postings: total = %d", stats["total_reported"])

            parsed = [p for raw in postings_raw if (p := _parse_posting(raw, settings.zonaprop_base_url))]
            stats["pages_ok"] += 1
            stats["total_found"] += len(parsed)

            log.info(
                "api_postings: pág %d → %d publicaciones (total: %d)",
                page_num, len(parsed), stats["total_found"],
            )

            if persist_fn is not None and parsed:
                await persist_fn(parsed, page_num)

    finally:
        await session.close()

    return stats
