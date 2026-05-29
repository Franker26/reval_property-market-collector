"""
Servicio de discovery: orquesta API Zonaprop + persistencia en DB.

Conecta el discovery loop de api_postings.py con el repository layer,
registrando runs, discovery_events y listing_entities.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import get_settings
from app.db.session import get_async_session_factory
from app.repositories import collection_errors as errors_repo
from app.repositories import collection_runs as runs_repo
from app.repositories import discovery_events as events_repo
from app.repositories import listings as listings_repo
from app.repositories import sources as sources_repo

log = logging.getLogger(__name__)


async def run_zonaprop_api_discovery(
    tipo_operacion: str = "1",
    max_pages: int = 50,
    source_code: str = "zonaprop",
) -> dict:
    """tipo_operacion: '1' = venta, '2' = alquiler."""
    """
    Corre discovery completo de Zonaprop API:
    - Crea collection_run.
    - Itera páginas de la API.
    - Persiste discovery_events y hace upsert de listing_entities.
    - Finaliza el run con stats.
    """
    from discovery.zonaprop.api_postings import discover

    factory = get_async_session_factory()
    settings = get_settings()

    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            log.error("discovery_service: fuente '%s' no encontrada en DB", source_code)
            return {"error": f"source '{source_code}' not found"}

        source_id = source.id
        run = await runs_repo.start(
            session,
            run_type="discovery",
            source_id=source_id,
            params={"tipo_operacion": tipo_operacion, "max_pages": max_pages},
        )
        await session.commit()
        run_id = run.id

    discovered_count = 0
    new_count = 0
    error_count = 0

    async def persist_page(postings: list[dict], page: int) -> None:
        nonlocal discovered_count, new_count, error_count
        async with factory() as session:
            for p in postings:
                try:
                    await events_repo.record(
                        session,
                        source_id=source_id,
                        url=p["canonical_url"],
                        method="api_postings",
                        external_id=p["external_id"],
                        page_number=page,
                        offset_value=(page - 1) * 30,
                        run_id=run_id,
                    )
                    entity = await listings_repo.upsert(
                        session,
                        source_id=source_id,
                        external_id=p["external_id"],
                        canonical_url=p["canonical_url"],
                        operation_type=p.get("operation_type"),
                        property_type=p.get("property_type"),
                        status="unknown",
                    )
                    if entity.last_seen_at is None:
                        new_count += 1
                    discovered_count += 1
                except Exception as exc:
                    error_count += 1
                    log.error("discovery_service: error persistiendo %s — %s", p.get("external_id"), exc)
                    try:
                        await errors_repo.record(
                            session,
                            error_type="unknown",
                            run_id=run_id,
                            source_id=source_id,
                            external_id=p.get("external_id"),
                            url=p.get("canonical_url"),
                            error_message=str(exc),
                        )
                    except Exception:
                        pass
            await session.commit()

    try:
        api_stats = await discover(
            tipo_operacion=tipo_operacion,
            max_pages=max_pages,
            persist_fn=persist_page,
        )
    except Exception as exc:
        log.error("discovery_service: fallo fatal en discover() — %s", exc)
        api_stats = {"pages_ok": 0, "pages_failed": 1, "total_found": 0}

    stats = {
        **api_stats,
        "discovered_count": discovered_count,
        "new_count": new_count,
        "error_count": error_count,
    }

    final_status = "success" if api_stats.get("pages_ok", 0) > 0 else "failed"
    if api_stats.get("stopped_early"):
        final_status = "partial"

    async with factory() as session:
        await runs_repo.finish(session, run_id, status=final_status, stats=stats)
        await session.commit()

    log.info(
        "discovery_service: run_id=%d status=%s discovered=%d new=%d errors=%d",
        run_id, final_status, discovered_count, new_count, error_count,
    )
    return {"run_id": run_id, "status": final_status, **stats}
