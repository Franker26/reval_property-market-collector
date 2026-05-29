#!/usr/bin/env python3
"""
scripts/zonaprop_probe_deep_pagination.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Valida si la API de Zonaprop restringe el acceso a páginas profundas,
igual que el sitio web (que bloquea después de la página 5).

Testea páginas: 5, 6, 10, 50, 100, 500, 1000, 5000, 10000, 20000

Uso:
    python scripts/zonaprop_probe_deep_pagination.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import get_settings
from discovery.zonaprop.api_postings import build_payload, _extract_postings, _extract_total

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("probe_deep_pagination")

_AR_TZ = timezone(timedelta(hours=-3))
_OUTPUT_PATH = _PROJECT_ROOT / "outputs" / "diagnostics" / "zonaprop_deep_pagination_probe.json"

_PAGES_TO_TEST = [5, 6, 10, 50, 100, 500, 1000, 5000, 10000, 20000]


async def probe_page(session, page: int, api_url: str) -> dict:
    payload = build_payload(page=page)
    t0 = time.monotonic()
    data = await session.post_json(api_url, payload)
    elapsed = time.monotonic() - t0

    if data is None:
        log.warning("  pág %5d → sin respuesta (%.2fs)", page, elapsed)
        return {"page": page, "status": "no_response", "elapsed_seconds": round(elapsed, 2)}

    if "__http_error__" in data:
        status = data["__http_error__"]
        log.warning("  pág %5d → HTTP %d (%.2fs)", page, status, elapsed)
        return {"page": page, "http_status": status, "status": "error", "elapsed_seconds": round(elapsed, 2)}

    postings = _extract_postings(data)
    total = _extract_total(data)
    paging = data.get("paging", {})
    first_id = str(postings[0].get("postingId", "")) if postings else None

    log.info(
        "  pág %5d → HTTP 200 | resultados=%d | total=%s | first_id=%s (%.2fs)",
        page, len(postings), total, first_id, elapsed,
    )

    return {
        "page": page,
        "http_status": 200,
        "status": "ok",
        "results_count": len(postings),
        "total_reported": total,
        "paging": paging,
        "first_id": first_id,
        "elapsed_seconds": round(elapsed, 2),
    }


async def main() -> int:
    settings = get_settings()
    api_url = settings.zonaprop_api_postings_url

    log.info("=" * 60)
    log.info("Zonaprop deep pagination probe")
    log.info("Páginas a testear: %s", _PAGES_TO_TEST)
    log.info("=" * 60)

    from sources.session_manager import create_zonaprop_session
    session = await create_zonaprop_session(
        warmup_url=settings.zonaprop_warmup_url,
        browser=None,
        user_agent=settings.zonaprop_user_agent,
        base_url=settings.zonaprop_base_url,
    )

    results = []
    try:
        for i, page in enumerate(_PAGES_TO_TEST):
            if i > 0:
                await asyncio.sleep(4)
            result = await probe_page(session, page, api_url)
            results.append(result)
            # Parar si empieza a bloquear
            if result.get("http_status") in (403, 429):
                log.error("Bloqueado en página %d — deteniendo probe", page)
                break
    finally:
        await session.close()

    # Análisis
    ok = [r for r in results if r.get("http_status") == 200]
    blocked = [r for r in results if r.get("http_status") in (403, 429)]
    max_ok_page = max((r["page"] for r in ok), default=0)

    analysis = {
        "pages_ok": len(ok),
        "pages_blocked": len(blocked),
        "max_accessible_page": max_ok_page,
        "conclusion": "sin_restriccion" if not blocked and len(ok) == len(results)
                      else f"bloqueado_desde_pagina_{blocked[0]['page']}" if blocked
                      else "parcial",
    }

    report = {
        "generated_at": datetime.now(_AR_TZ).isoformat(),
        "api_url": api_url,
        "pages_tested": _PAGES_TO_TEST,
        "analysis": analysis,
        "results": results,
    }

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("=" * 60)
    log.info("Conclusión: %s", analysis["conclusion"])
    log.info("Página máxima accesible: %d", max_ok_page)
    log.info("Reporte → %s", _OUTPUT_PATH)
    log.info("=" * 60)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
