"""
Scheduler: segment_discovery semanal.

Sábados entre 10:00 y 14:00 AR — construye el árbol adaptativo de segmentos.
"""
from __future__ import annotations

import asyncio
import logging
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _job_segment_discovery() -> None:
    delay_minutes = random.uniform(0, 240)
    log.info(
        "scheduler: segment_discovery arranca en %.0f minutos (ventana 10:00–14:00 AR)",
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


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        settings = get_settings()
        _scheduler = AsyncIOScheduler(timezone=settings.collector_timezone)

        _scheduler.add_job(
            _job_segment_discovery,
            trigger=CronTrigger(day_of_week="sat", hour=10, minute=0,
                                timezone=settings.collector_timezone),
            id="weekly_segment_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        log.info("scheduler: 1 job configurado (weekly_segment_discovery)")

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
