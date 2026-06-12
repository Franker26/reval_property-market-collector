"""
Scheduler: segment_discovery semanal + url_discovery L-V y domingo con ventana horaria.
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
        result = await run_segment_discovery(mode="scheduled")
        log.info("scheduler: segment_discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en segment_discovery — %s", exc)


async def _run_url_discovery_window(
    delay_max_secs: int,
    stop_hour: int,
    stop_minute_base: int,
    stop_minute_jitter: int = 30,
) -> None:
    """
    Lógica compartida de url_discovery con ventana horaria configurable.

    delay_max_secs    — máximo de segundos de espera antes de arrancar
    stop_hour         — hora de corte (AR)
    stop_minute_base  — minuto base de corte
    stop_minute_jitter— variación aleatoria en minutos sobre stop_minute_base
    """
    from zoneinfo import ZoneInfo
    settings = get_settings()
    ar_tz = ZoneInfo(settings.collector_timezone)

    delay_secs = random.uniform(0, delay_max_secs)
    log.info("scheduler: url_discovery arranca en %.0f segundos", delay_secs)
    await asyncio.sleep(delay_secs)

    now = datetime.now(ar_tz)
    stop_at = now.replace(
        hour=stop_hour, minute=stop_minute_base, second=0, microsecond=0
    ) + timedelta(minutes=random.randint(0, stop_minute_jitter))
    log.info("scheduler: url_discovery iniciando — stop_at=%s", stop_at.strftime("%H:%M %Z"))

    try:
        from app.services.discovery_service import run_url_discovery_window
        result = await run_url_discovery_window(stop_at=stop_at, mode="scheduled")
        log.info("scheduler: url_discovery finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en url_discovery — %s", exc)


async def _job_url_discovery_weekday() -> None:
    """L-V: arranca entre 06:00 y 07:00 AR, corta entre 18:30 y 19:00 AR."""
    await _run_url_discovery_window(
        delay_max_secs=3600,
        stop_hour=18,
        stop_minute_base=30,
        stop_minute_jitter=30,
    )


async def _job_url_discovery_sunday() -> None:
    """Domingo: arranca entre 10:00 y 10:30 AR, corta entre 16:00 y 16:30 AR."""
    await _run_url_discovery_window(
        delay_max_secs=1800,
        stop_hour=16,
        stop_minute_base=0,
        stop_minute_jitter=30,
    )


async def _job_build_location_normalization() -> None:
    log.info("scheduler: build_location_normalization iniciando")
    try:
        from jobs.build_location_normalization import run
        await run(mode="incremental", batch_size=500, source_id=None, dry_run=False)
        log.info("scheduler: build_location_normalization finalizado")
    except Exception as exc:
        log.error("scheduler: error en build_location_normalization — %s", exc)


async def _job_build_market_facts() -> None:
    log.info("scheduler: build_market_facts iniciando")
    try:
        from jobs.build_market_facts import run
        await run(mode="incremental", batch_size=500, source_id=None, dry_run=False)
        log.info("scheduler: build_market_facts finalizado")
    except Exception as exc:
        log.error("scheduler: error en build_market_facts — %s", exc)


async def _job_refresh_monitor() -> None:
    from app.core.config import get_refresh_config
    if not get_refresh_config().enabled:
        log.info("scheduler: refresh_monitor deshabilitado (REFRESH_MONITOR_ENABLED=false) — saltando")
        return
    log.info("scheduler: refresh_monitor iniciando")
    try:
        from app.services.discovery_service import run_refresh_monitor
        result = await run_refresh_monitor(mode="scheduled")
        log.info("scheduler: refresh_monitor finalizado — %s", result)
    except Exception as exc:
        log.error("scheduler: error en refresh_monitor — %s", exc)


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
            _job_url_discovery_weekday,
            trigger=CronTrigger(day_of_week="mon-fri", hour=6, minute=0,
                                timezone=settings.collector_timezone),
            id="weekday_url_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            _job_url_discovery_sunday,
            trigger=CronTrigger(day_of_week="sun", hour=10, minute=0,
                                timezone=settings.collector_timezone),
            id="sunday_url_discovery",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            _job_build_location_normalization,
            trigger=CronTrigger(hour="0,6,12,18", minute=0,
                                timezone=settings.collector_timezone),
            id="build_location_normalization_6h",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            _job_build_market_facts,
            trigger=CronTrigger(hour="0,6,12,18", minute=30,
                                timezone=settings.collector_timezone),
            id="build_market_facts_6h",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        from app.core.config import get_refresh_config
        refresh_enabled = get_refresh_config().enabled
        if refresh_enabled:
            # Varias veces/día dentro de la ventana de url_discovery (L-V 06:00–18:30 AR),
            # para que haya consumidores tras el reencolado.
            _scheduler.add_job(
                _job_refresh_monitor,
                trigger=CronTrigger(day_of_week="mon-sun", hour="7,11,15", minute=15,
                                    timezone=settings.collector_timezone),
                id="refresh_monitor",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

        log.info(
            "scheduler: %d jobs configurados"
            " (weekly_segment_discovery, weekday_url_discovery, sunday_url_discovery,"
            " build_location_normalization_6h, build_market_facts_6h%s)",
            6 if refresh_enabled else 5,
            ", refresh_monitor" if refresh_enabled else "",
        )

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
