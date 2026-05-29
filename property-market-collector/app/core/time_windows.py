"""Control de ventanas horarias operativas."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def is_within_operational_window() -> bool:
    """True si la hora actual está dentro del rango permitido (config)."""
    settings = get_settings()
    tz = ZoneInfo(settings.collector_timezone)
    now = datetime.now(tz)
    return settings.collector_allowed_start_hour <= now.hour < settings.collector_allowed_end_hour


def current_hour_in_tz() -> int:
    settings = get_settings()
    tz = ZoneInfo(settings.collector_timezone)
    return datetime.now(tz).hour
