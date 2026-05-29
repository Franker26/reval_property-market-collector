"""
Gestión de sesión con Playwright para requests API.

Problema: Cloudflare Bot Management bloquea httpx aunque tenga cf_clearance,
porque analiza el TLS fingerprint (JA3). httpx usa el SSL de Python, que
es identificable como no-browser.

Solución: usar Playwright's APIRequestContext (context.request) para los
POST a la API. Hace requests HTTP reales usando el stack de red del browser
(TLS correcto, cookies incluidas), sin renderizar JavaScript.

Es mucho más liviano que cargar una página completa.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext

log = logging.getLogger(__name__)

_WARMUP_WAIT_MS = 8_000   # tiempo para que el JS challenge de Cloudflare ejecute


class ZonapropSession:
    """
    Encapsula un BrowserContext de Playwright para hacer requests API
    a Zonaprop con TLS fingerprint correcto y cookies de sesión.
    """

    def __init__(self, ctx: BrowserContext, cf_clearance: bool = False) -> None:
        self._ctx = ctx
        self.cf_clearance = cf_clearance

    async def post_json(self, url: str, payload: dict, extra_headers: Optional[dict] = None) -> Optional[dict]:
        """
        POST JSON a la URL usando el context de Playwright.
        Devuelve el JSON de respuesta, o None si falla.
        """
        # Headers que envía Chrome real para un fetch() mismo-origen
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9",
            "Content-Type": "application/json",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = await self._ctx.request.post(
                url,
                data=json.dumps(payload),
                headers=headers,
            )
            if resp.status == 200:
                return await resp.json()
            log.warning("session.post_json: HTTP %d → %s", resp.status, url)
            return {"__http_error__": resp.status}
        except Exception as exc:
            log.error("session.post_json: error en %s — %s", url, exc)
            return None

    async def close(self) -> None:
        await self._ctx.close()


async def create_zonaprop_session(
    warmup_url: str,
    browser: Browser,
    user_agent: str,
    base_url: str,
) -> ZonapropSession:
    """
    Crea una sesión navegando warmup_url con Playwright para resolver
    el Cloudflare challenge y obtener las cookies necesarias.
    """
    ctx: BrowserContext = await browser.new_context(
        user_agent=user_agent,
        locale="es-AR",
        extra_http_headers={
            "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": base_url,
            "Referer": warmup_url,
        },
    )

    log.info("session_manager: navegando %s para resolver Cloudflare…", warmup_url)
    page = await ctx.new_page()
    try:
        # "load" espera que el JS ejecute (necesario para el challenge de Cloudflare).
        # Si falla por timeout (IP muy bloqueada), lo capturamos y continuamos.
        try:
            await page.goto(warmup_url, wait_until="load", timeout=30_000)
        except Exception as nav_exc:
            log.warning("session_manager: navegación falló (%s) — continuando con cookies parciales", nav_exc)

        # Esperar a que el JS challenge de Cloudflare complete
        await page.wait_for_timeout(_WARMUP_WAIT_MS)

        cookies = await ctx.cookies()
        cf_ok = any(c["name"] == "cf_clearance" for c in cookies)
        log.info(
            "session_manager: %d cookies — cf_clearance=%s",
            len(cookies),
            "✓" if cf_ok else "✗",
        )
        if not cf_ok:
            log.warning("session_manager: cf_clearance no obtenida — las API calls pueden fallar con 403")
    finally:
        await page.close()

    return ZonapropSession(ctx, cf_clearance=cf_ok)
