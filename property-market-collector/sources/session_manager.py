"""
Gestión de sesión HTTP con TLS fingerprint de Chrome real.

Problema: Cloudflare Bot Management detecta Python/OpenSSL en Linux
(servidores, Docker) por el JA3 fingerprint y devuelve 403.
El JA3 de macOS pasa, pero en Docker/Linux es bloqueado.

Solución: curl_cffi — wrapper de curl-impersonate que usa el stack TLS
de Chrome real. Produce el mismo JA3 que un browser, en cualquier OS.
Sin Playwright, sin warmup, sin cf_clearance necesario.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from curl_cffi.requests import AsyncSession

log = logging.getLogger(__name__)

# Versión de Chrome a impersonar — debe coincidir con el User-Agent
_IMPERSONATE = "chrome120"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.zonaprop.com.ar",
    "Referer": "https://www.zonaprop.com.ar/inmuebles-venta.html",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class ZonapropSession:
    """
    Sesión HTTP con TLS fingerprint de Chrome via curl_cffi.
    No requiere Playwright ni warmup.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.cf_clearance = True  # curl_cffi bypasa el check — siempre OK

    _REQUEST_TIMEOUT = 30  # segundos — aplica a toda la request, no solo al connect

    async def post_json(
        self,
        url: str,
        payload: dict,
        extra_headers: Optional[dict] = None,
    ) -> Optional[dict]:
        headers = dict(_BASE_HEADERS)
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = await asyncio.wait_for(
                self._session.post(
                    url,
                    data=json.dumps(payload),
                    headers=headers,
                    impersonate=_IMPERSONATE,
                    timeout=30,
                ),
                timeout=self._REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
            log.warning("session.post_json: HTTP %d → %s", resp.status_code, url)
            return {"__http_error__": resp.status_code}
        except asyncio.TimeoutError:
            log.error("session.post_json: timeout (%ds) — %s", self._REQUEST_TIMEOUT, url)
            return None
        except Exception as exc:
            log.error("session.post_json: error en %s — %s", url, exc)
            return None

    async def close(self) -> None:
        await self._session.close()


async def create_zonaprop_session(
    warmup_url: str,
    browser: Any,
    user_agent: str,
    base_url: str,
) -> ZonapropSession:
    """
    Crea una sesión con TLS fingerprint de Chrome.
    Los parámetros warmup_url/browser/user_agent se mantienen por
    compatibilidad con la firma anterior pero ya no se usan.
    """
    log.info("session_manager: creando sesión curl_cffi (Chrome TLS fingerprint)")
    session = AsyncSession()
    return ZonapropSession(session)
