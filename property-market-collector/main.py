import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.core.logging_setup import configure_root_logger
from app.core.log_buffer import setup_log_buffer

configure_root_logger()
setup_log_buffer()

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
            from app.db.models import Base
            from app.db.seed import seed_sources
            from app.db.session import get_async_engine, get_async_session_factory
            from app.services.scheduler_service import start_scheduler

            engine = get_async_engine()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            factory = get_async_session_factory()
            async with factory() as session:
                await seed_sources(session)

            # Resetear segmentos que quedaron en 'running' si el container
            # fue reiniciado a mitad de una ejecución.
            from app.repositories.zonaprop import scan_queue as _sq_repo
            async with factory() as session:
                async with session.begin():
                    reset_count = await _sq_repo.reset_all_running(session)
            if reset_count > 0:
                log.warning("startup: %d segmentos en 'running' reseteados a 'pending'", reset_count)

            # Marcar collection_runs que quedaron en 'running' como 'failed'
            from app.repositories import collection_runs as _runs_repo
            async with factory() as session:
                async with session.begin():
                    await _runs_repo.reset_stale_running_runs(session)

            start_scheduler()

            from app.core.alerts import setup_alert_dispatcher, dispatch
            setup_alert_dispatcher()

            import asyncio
            asyncio.create_task(dispatch(
                "service_started", "warning",
                "Reval Market Intelligence levantó correctamente",
                {"env": os.getenv("APP_ENV", "development"), "db": "ok", "scheduler": "ok"},
            ))

            log.info("DB, scheduler y alertas iniciados correctamente")
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

# ── Router de logs (siempre disponible) ───────────────────────────────────────

from app.routers import logs as logs_router
app.include_router(logs_router.router)

# ── Routers de la API interna (disponibles solo si hay DB) ────────────────────

if _db_available and WRITE_DATABASE:
    try:
        from app.routers import listings, runs, errors, sources as sources_router, discovery
        from app.routers import health as health_router, ops as ops_router
        app.include_router(listings.router)
        app.include_router(runs.router)
        app.include_router(errors.router)
        app.include_router(sources_router.router)
        app.include_router(discovery.router)
        app.include_router(health_router.router)
        app.include_router(ops_router.router)
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

    return result
