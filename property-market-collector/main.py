import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import sources
from sources import browser as _browser_mod

log = logging.getLogger(__name__)

SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "")
WRITE_DATABASE = os.getenv("WRITE_DATABASE", "true").lower() == "true"

# ── DB / scheduler (solo si hay DATABASE_URL configurada) ─────────────────────

_db_available = bool(os.getenv("DATABASE_URL"))

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _browser_mod.get_browser()

    if _db_available and WRITE_DATABASE:
        try:
            from app.db.seed import seed_sources
            from app.db.session import get_async_session_factory
            from app.services.scheduler_service import start_scheduler

            factory = get_async_session_factory()
            async with factory() as session:
                await seed_sources(session)

            start_scheduler()
            log.info("DB y scheduler iniciados correctamente")
        except Exception as exc:
            log.warning("No se pudo inicializar DB/scheduler: %s", exc)

    yield

    await _browser_mod.close()

    if _db_available and WRITE_DATABASE:
        try:
            from app.services.scheduler_service import stop_scheduler
            stop_scheduler()
        except Exception:
            pass


app = FastAPI(title="Reval Market Intelligence", lifespan=lifespan)

# ── Routers de la API interna (disponibles solo si hay DB) ────────────────────

if _db_available and WRITE_DATABASE:
    try:
        from app.routers import listings, runs, errors, sources as sources_router, discovery
        app.include_router(listings.router)
        app.include_router(runs.router)
        app.include_router(errors.router)
        app.include_router(sources_router.router)
        app.include_router(discovery.router)
    except Exception as exc:
        log.warning("No se pudieron registrar routers de DB: %s", exc)


# ── Endpoints base ────────────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    url: str


@app.get("/health")
def health():
    return {
        "status": "ok",
        "sources": sources.SUPPORTED_SOURCES,
        "db_enabled": _db_available and WRITE_DATABASE,
    }


@app.post("/extract")
async def extract(body: ExtractRequest, authorization: str = Header(default="")):
    if SERVICE_TOKEN and authorization != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(401, "Unauthorized")

    url = body.url.strip()
    async with httpx.AsyncClient(headers=_BROWSER_HEADERS, follow_redirects=True, timeout=20) as client:
        result = await sources.extract(url, client)

    if _db_available and WRITE_DATABASE:
        try:
            await _persist_snapshot(url, result)
        except Exception as exc:
            log.warning("No se pudo persistir snapshot en DB: %s", exc)

    return result


async def _persist_snapshot(url: str, payload: dict) -> None:
    """Persiste el resultado de /extract como snapshot en DB si hay DB disponible."""
    from app.core.hashing import compute_content_hash
    from app.db.session import get_async_session_factory
    from app.repositories import listings as listings_repo
    from app.repositories import snapshots as snapshots_repo
    from app.repositories import sources as sources_repo

    source_code = payload.get("source", "")
    if not source_code:
        return

    factory = get_async_session_factory()
    async with factory() as session:
        source = await sources_repo.get_by_code(session, source_code)
        if source is None:
            return

        external_id = payload.get("external_id") or ""
        if not external_id:
            return

        listing = await listings_repo.upsert(
            session,
            source_id=source.id,
            external_id=external_id,
            canonical_url=url,
            operation_type=(payload.get("listing") or {}).get("operation_type"),
            property_type=(payload.get("property") or {}).get("tipo"),
            status="active",
        )

        new_hash = compute_content_hash(payload)
        changed = await snapshots_repo.has_changed(session, listing.id, new_hash)

        snapshot = await snapshots_repo.create(session, listing.id, payload)
        await listings_repo.mark_success(
            session, listing.id, snapshot.id, status="active", changed=changed
        )
        await session.commit()
        log.info("snapshot persistido: listing_id=%d changed=%s", listing.id, changed)
