"""
Scheduler: segment_discovery semanal + url_discovery L-V con ventana horaria.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta

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


async def _job_url_discovery() -> None:
    from zoneinfo import ZoneInfo
    settings = get_settings()
    ar_tz = ZoneInfo(settings.collector_timezone)

    delay_secs = random.uniform(0, 3600)
    log.info("scheduler: url_discovery arranca en %.0f segundos", delay_secs)
    await asyncio.sleep(delay_secs)

    now = datetime.now(ar_tz)
    stop_at = now.replace(hour=18, minute=30, second=0, microsecond=0) + timedelta(
        minutes=random.randint(0, 30)
    )
    log.info("scheduler: url_discovery iniciando — stop_at=%s", stop_at.strftime("%H:%M %Z"))

    try:
        from app.services.discovery_service import run_url_discovery_window
        result = await run_url_discovery_window(stop_at=stop_at)
        log.info("scheduler: url_discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en url_discovery — %s", exc)


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

        _scheduler.add_job(
            _job_url_discovery,
            trigger=CronTrigger(day_of_week="mon-fri", hour=6, minute=0,
                                timezone=settings.collector_timezone),
            id="weekday_url_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        log.info("scheduler: 2 jobs configurados (weekly_segment_discovery, weekday_url_discovery)")

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
