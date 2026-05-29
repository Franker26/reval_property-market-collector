"""
Scheduler: orquesta el pipeline de 3 fases de discovery.

  Semanal  → segment_discovery   (construye árbol adaptativo)
  Diario   → url_discovery       (extrae URLs de segmentos hoja)
  Diario   → incremental_monitor (detecta cambios en counts)
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.time_windows import is_within_operational_window

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


# ── Jobs ──────────────────────────────────────────────────────────────────────


async def _job_segment_discovery() -> None:
    import random
    delay_minutes = random.uniform(0, 240)  # hasta 4 horas → ventana 10:00–14:00
    log.info(
        "scheduler: segment_discovery programado — arranca en %.0f minutos (ventana 10:00–14:00)",
        delay_minutes,
    )
    await asyncio.sleep(delay_minutes * 60)
    log.info("scheduler: iniciando segment_discovery")
    try:
        from app.services.discovery_service import run_segment_discovery
        result = await run_segment_discovery()
        log.info("scheduler: segment_discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en segment_discovery — %s", exc)


async def _job_url_discovery() -> None:
    if not is_within_operational_window():
        log.info("scheduler: fuera de ventana — omitiendo url_discovery")
        return
    log.info("scheduler: iniciando url_discovery")
    try:
        from app.services.discovery_service import run_url_discovery
        result = await run_url_discovery()
        log.info("scheduler: url_discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en url_discovery — %s", exc)


async def _job_incremental_monitor() -> None:
    if not is_within_operational_window():
        log.info("scheduler: fuera de ventana — omitiendo incremental_monitor")
        return
    log.info("scheduler: iniciando incremental_monitor")
    try:
        from app.services.discovery_service import run_incremental_monitor
        result = await run_incremental_monitor()
        log.info("scheduler: incremental_monitor finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en incremental_monitor — %s", exc)


# ── Configuración del scheduler ───────────────────────────────────────────────


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        settings = get_settings()
        _scheduler = AsyncIOScheduler(timezone=settings.collector_timezone)

        # Semanal: sábados 10:00 AM — el job duerme un offset aleatorio (0–4hs)
        # para ejecutar en algún momento entre las 10:00 y las 14:00
        _scheduler.add_job(
            _job_segment_discovery,
            trigger=CronTrigger(day_of_week="sat", hour=10, minute=0,
                                timezone=settings.collector_timezone),
            id="weekly_segment_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Diario: 08:00 AM — extracción de URLs tras el segment_discovery semanal
        _scheduler.add_job(
            _job_url_discovery,
            trigger=CronTrigger(hour=8, minute=0),
            id="daily_url_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Diario: 20:00 PM — monitoreo de cambios en fin de jornada
        _scheduler.add_job(
            _job_incremental_monitor,
            trigger=CronTrigger(hour=20, minute=0),
            id="daily_incremental_monitor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        log.info("scheduler: configurado con %d jobs", len(_scheduler.get_jobs()))

    return _scheduler


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        log.info("scheduler: iniciado")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler: detenido")
