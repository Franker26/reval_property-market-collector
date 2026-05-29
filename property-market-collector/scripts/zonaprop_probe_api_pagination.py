#!/usr/bin/env python3
"""
scripts/zonaprop_probe_api_pagination.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Diagnóstico de paginación de POST /rplis-api/postings de Zonaprop.

Usa Playwright para warmup (resolver Cloudflare) y para las requests a la API
(TLS fingerprint correcto). httpx es bloqueado por Cloudflare por fingerprint.

Prueba páginas 1, 2 y 3. Confirma paginación con `pagina` y detecta overlap.
Genera reporte en outputs/diagnostics/zonaprop_api_pagination_probe.json.

Uso:
    python scripts/zonaprop_probe_api_pagination.py
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
log = logging.getLogger("probe_api_pagination")

_AR_TZ = timezone(timedelta(hours=-3))
_OUTPUT_PATH = _PROJECT_ROOT / "outputs" / "diagnostics" / "zonaprop_api_pagination_probe.json"


def _extract_ids(data: dict) -> list[str]:
    postings = _extract_postings(data)
    return [str(p.get("postingId") or p.get("id") or "") for p in postings if p.get("postingId") or p.get("id")]


async def probe_one(session, label: str, page: int, api_url: str) -> dict:
    payload = build_payload(page=page)
    log.info("Probando [%s] pagina=%d", label, page)

    t0 = time.monotonic()
    data = await session.post_json(api_url, payload)
    elapsed = time.monotonic() - t0

    if data is None:
        log.error("  → sin respuesta (%.2fs)", elapsed)
        return {"label": label, "pagina": page, "error": "no_response", "elapsed_seconds": round(elapsed, 2)}

    if "__http_error__" in data:
        status = data["__http_error__"]
        log.warning("  → HTTP %d (%.2fs)", status, elapsed)
        return {"label": label, "pagina": page, "http_status": status, "error": f"HTTP {status}", "elapsed_seconds": round(elapsed, 2)}

    ids = _extract_ids(data)
    total = _extract_total(data)
    paging = data.get("paging", {})

    log.info(
        "  → HTTP 200 | total=%s | paging=%s | resultados=%d | first=%s last=%s | %.2fs",
        total, paging, len(ids), ids[0] if ids else None, ids[-1] if ids else None, elapsed,
    )

    return {
        "label": label,
        "pagina": page,
        "http_status": 200,
        "results_count": len(ids),
        "total_reported": total,
        "paging": paging,
        "first_id": ids[0] if ids else None,
        "last_id": ids[-1] if ids else None,
        "all_ids": ids,
        "elapsed_seconds": round(elapsed, 2),
        "response_keys": list(data.keys()),
    }


def _analyze(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("http_status") == 200]
    if len(ok) < 2:
        return {"pagination_signal": "insufficient_data", "overlap_between_pages": None}

    id_sets = {r["pagina"]: set(r.get("all_ids") or []) for r in ok}
    pages = sorted(id_sets)
    overlaps = {}
    for i in range(len(pages) - 1):
        pa, pb = pages[i], pages[i + 1]
        overlap = id_sets[pa] & id_sets[pb]
        overlaps[f"p{pa} ∩ p{pb}"] = len(overlap)

    signal = "pagina_works_no_overlap" if all(v == 0 for v in overlaps.values()) else "overlap_detected"
    return {"pagination_signal": signal, "overlap_between_pages": overlaps}


async def main() -> int:
    settings = get_settings()
    api_url = settings.zonaprop_api_postings_url

    log.info("=" * 60)
    log.info("Zonaprop API pagination probe (curl_cffi / Chrome TLS)")
    log.info("API URL: %s", api_url)
    log.info("=" * 60)

    from sources.session_manager import create_zonaprop_session

    # curl_cffi maneja el TLS fingerprint de Chrome — sin Playwright ni warmup
    session = await create_zonaprop_session(
        warmup_url=settings.zonaprop_warmup_url,
        browser=None,
        user_agent=settings.zonaprop_user_agent,
        base_url=settings.zonaprop_base_url,
    )

    results: list[dict] = []
    try:
        for i, (label, page) in enumerate([("pagina_1", 1), ("pagina_2", 2), ("pagina_3", 3)]):
            if i > 0:
                await asyncio.sleep(4)
            result = await probe_one(session, label=label, page=page, api_url=api_url)
            results.append(result)
    finally:
        await session.close()

    analysis = _analyze(results)

    report = {
        "generated_at": datetime.now(_AR_TZ).isoformat(),
        "api_url": api_url,
        "approach": "curl_cffi_chrome_tls",
        "analysis": analysis,
        "results": results,
    }

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("=" * 60)
    log.info("Reporte → %s", _OUTPUT_PATH)
    log.info("Señal de paginación: %s", analysis["pagination_signal"])
    log.info("=" * 60)

    return 0 if any(r.get("http_status") == 200 for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
