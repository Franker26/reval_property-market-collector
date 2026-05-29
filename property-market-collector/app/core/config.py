"""Configuración centralizada del servicio."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional


class Settings:
    app_env: str
    database_url: str
    sync_database_url: str
    redis_url: str
    service_token: str

    # Colector
    collector_timezone: str
    collector_allowed_start_hour: int
    collector_allowed_end_hour: int

    # Zonaprop
    zonaprop_base_url: str
    zonaprop_api_postings_url: str
    zonaprop_enable_session_warmup: bool
    zonaprop_warmup_url: str
    zonaprop_user_agent: str

    # Flags de escritura
    write_jsonl_output: bool
    write_database: bool

    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development")
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://reval:reval@localhost:5432/reval_mi",
        )
        self.sync_database_url = os.getenv(
            "SYNC_DATABASE_URL",
            "postgresql://reval:reval@localhost:5432/reval_mi",
        )
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.service_token = os.getenv("SERVICE_TOKEN", "")

        self.collector_timezone = os.getenv(
            "COLLECTOR_TIMEZONE", "America/Argentina/Buenos_Aires"
        )
        self.collector_allowed_start_hour = int(
            os.getenv("COLLECTOR_ALLOWED_START_HOUR", "9")
        )
        self.collector_allowed_end_hour = int(
            os.getenv("COLLECTOR_ALLOWED_END_HOUR", "22")
        )

        self.zonaprop_base_url = os.getenv(
            "ZONAPROP_BASE_URL", "https://www.zonaprop.com.ar"
        )
        self.zonaprop_api_postings_url = os.getenv(
            "ZONAPROP_API_POSTINGS_URL",
            "https://www.zonaprop.com.ar/rplis-api/postings",
        )
        self.zonaprop_enable_session_warmup = (
            os.getenv("ZONAPROP_ENABLE_SESSION_WARMUP", "true").lower() == "true"
        )
        self.zonaprop_warmup_url = os.getenv(
            "ZONAPROP_WARMUP_URL",
            "https://www.zonaprop.com.ar/inmuebles-venta.html",
        )
        self.zonaprop_user_agent = os.getenv(
            "ZONAPROP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )

        self.write_jsonl_output = (
            os.getenv("WRITE_JSONL_OUTPUT", "true").lower() == "true"
        )
        self.write_database = (
            os.getenv("WRITE_DATABASE", "true").lower() == "true"
        )

    @property
    def browser_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.zonaprop_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }

    @property
    def zonaprop_api_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.zonaprop_user_agent,
            "Origin": self.zonaprop_base_url,
            "Referer": self.zonaprop_warmup_url,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
