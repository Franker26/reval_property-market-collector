"""Configuración centralizada del servicio."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


class Settings:
    app_env: str
    database_url: str
    sync_database_url: str
    service_token: str
    reval_mi_api_key: str

    # Colector
    collector_timezone: str

    # Zonaprop
    zonaprop_base_url: str
    zonaprop_api_postings_url: str
    zonaprop_enable_session_warmup: bool
    zonaprop_warmup_url: str
    zonaprop_user_agent: str

    # Rate limiter de url_discovery (Zonaprop API)
    zonaprop_url_discovery_min_delay: float
    zonaprop_url_discovery_max_delay: float
    zonaprop_url_discovery_burst_limit: int

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
        self.service_token = os.getenv("SERVICE_TOKEN", "")
        self.reval_mi_api_key = os.getenv("REVAL_MI_API_KEY", "")

        self.collector_timezone = os.getenv(
            "COLLECTOR_TIMEZONE", "America/Argentina/Buenos_Aires"
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

        self.zonaprop_url_discovery_min_delay = float(
            os.getenv("ZONAPROP_URL_DISCOVERY_MIN_DELAY", "3")
        )
        self.zonaprop_url_discovery_max_delay = float(
            os.getenv("ZONAPROP_URL_DISCOVERY_MAX_DELAY", "9")
        )
        self.zonaprop_url_discovery_burst_limit = int(
            os.getenv("ZONAPROP_URL_DISCOVERY_BURST_LIMIT", "10")
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


@dataclass(frozen=True)
class RefreshConfig:
    """
    Parámetros del refresh rotativo priorizado de segmentos (Etapa B).

    Principios:
      - El score decide URGENCIA, nunca si un segmento se refresca: todo segmento
        tiene un gap máximo de frescura definido por negocio (cold = máximo tolerable).
      - Churn observado (url_discovery) es la señal principal; volatilidad de
        total_count y volumen son secundarias.
      - Sin evidencia de churn (samples < min_churn_samples) => tier 'unknown'
        (exploración), nunca cold por falta de datos.
    """
    enabled: bool
    gap_hours_hot: float
    gap_hours_warm: float
    gap_hours_cold: float
    gap_hours_unknown: float
    max_segments_per_cycle: int
    max_pages_per_cycle: int
    postings_per_page: int
    weight_churn: float
    weight_volatility: float
    weight_volume: float
    churn_cap: float
    churn_ewma_alpha: float
    min_churn_samples: int
    hot_score_threshold: float
    warm_score_threshold: float
    high_volume_threshold: int
    volatility_lookback_snapshots: int
    volatility_cap: float
    min_age_hours: float
    inherit_churn_on_split: bool
    inherited_churn_samples_cap: int
    volume_norm_divisor: float = 2000.0

    def gap_hours_for(self, tier: str) -> float:
        return {
            "hot": self.gap_hours_hot,
            "warm": self.gap_hours_warm,
            "cold": self.gap_hours_cold,
            "unknown": self.gap_hours_unknown,
        }[tier]


@lru_cache(maxsize=1)
def get_refresh_config() -> RefreshConfig:
    return RefreshConfig(
        enabled=os.getenv("REFRESH_MONITOR_ENABLED", "true").lower() == "true",
        gap_hours_hot=float(os.getenv("REFRESH_GAP_HOURS_HOT", "24")),
        gap_hours_warm=float(os.getenv("REFRESH_GAP_HOURS_WARM", "72")),
        gap_hours_cold=float(os.getenv("REFRESH_GAP_HOURS_COLD", "168")),
        gap_hours_unknown=float(os.getenv("REFRESH_GAP_HOURS_UNKNOWN", "72")),
        max_segments_per_cycle=int(os.getenv("REFRESH_MAX_SEGMENTS_PER_CYCLE", "50")),
        max_pages_per_cycle=int(os.getenv("REFRESH_MAX_PAGES_PER_CYCLE", "1500")),
        postings_per_page=int(os.getenv("REFRESH_POSTINGS_PER_PAGE", "20")),
        weight_churn=float(os.getenv("REFRESH_WEIGHT_CHURN", "0.50")),
        weight_volatility=float(os.getenv("REFRESH_WEIGHT_VOLATILITY", "0.25")),
        weight_volume=float(os.getenv("REFRESH_WEIGHT_VOLUME", "0.25")),
        churn_cap=float(os.getenv("REFRESH_CHURN_CAP", "0.05")),
        churn_ewma_alpha=float(os.getenv("REFRESH_CHURN_EWMA_ALPHA", "0.50")),
        min_churn_samples=int(os.getenv("REFRESH_MIN_CHURN_SAMPLES", "1")),
        hot_score_threshold=float(os.getenv("REFRESH_HOT_SCORE_THRESHOLD", "0.70")),
        warm_score_threshold=float(os.getenv("REFRESH_WARM_SCORE_THRESHOLD", "0.35")),
        high_volume_threshold=int(os.getenv("REFRESH_HIGH_VOLUME_THRESHOLD", "1500")),
        volatility_lookback_snapshots=int(os.getenv("REFRESH_VOLATILITY_LOOKBACK_SNAPSHOTS", "5")),
        volatility_cap=float(os.getenv("REFRESH_VOLATILITY_CAP", "0.10")),
        min_age_hours=float(os.getenv("REFRESH_MIN_AGE_HOURS", "12")),
        inherit_churn_on_split=os.getenv("REFRESH_INHERIT_CHURN_ON_SPLIT", "true").lower() == "true",
        # Regla: cap = min_churn_samples − 1. El prior heredado nunca habilita
        # score v2 por sí solo; solo inicializa el EWMA al llegar churn propio.
        inherited_churn_samples_cap=int(os.getenv("REFRESH_INHERITED_CHURN_SAMPLES_CAP", "0")),
    )


@dataclass(frozen=True)
class FullScanConfig:
    """
    Full scan de salida en vivo (baseline + compare). Apagado por defecto:
    solo corre con FULL_SCAN_ENABLED=true Y un batch_id explícito.
    """
    enabled: bool
    max_pages_per_cycle: int
    postings_per_page: int
    batch_id: str


@lru_cache(maxsize=1)
def get_full_scan_config() -> FullScanConfig:
    return FullScanConfig(
        enabled=os.getenv("FULL_SCAN_ENABLED", "false").lower() == "true",
        max_pages_per_cycle=int(os.getenv("FULL_SCAN_MAX_PAGES_PER_CYCLE", "1500")),
        postings_per_page=int(os.getenv("FULL_SCAN_POSTINGS_PER_PAGE", "20")),
        batch_id=os.getenv("FULL_SCAN_BATCH_ID", ""),
    )
