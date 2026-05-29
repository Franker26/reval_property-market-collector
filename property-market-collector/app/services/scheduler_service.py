"""
Scheduler mínimo con APScheduler.
Corre los jobs de discovery y revisit en ventanas horarias configuradas.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.time_windows import is_within_operational_window

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _job_weekly_zonaprop_discovery() -> None:
    if not is_within_operational_window():
        log.info("scheduler: fuera de ventana horaria — omitiendo discovery")
        return
    log.info("scheduler: iniciando weekly_zonaprop_api_discovery")
    try:
        from app.services.discovery_service import run_zonaprop_api_discovery
        result = await run_zonaprop_api_discovery(max_pages=100)
        log.info("scheduler: discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en discovery — %s", exc)


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        settings = get_settings()
        _scheduler = AsyncIOScheduler(timezone=settings.collector_timezone)

        # Discovery semanal: lunes a las 10:00 AM (hora Argentina)
        _scheduler.add_job(
            _job_weekly_zonaprop_discovery,
            trigger=CronTrigger(day_of_week="mon", hour=10, minute=0),
            id="weekly_zonaprop_api_discovery",
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
