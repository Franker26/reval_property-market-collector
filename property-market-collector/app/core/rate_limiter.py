"""Rate limiter adaptativo con jitter y cooldowns por fuente."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class RateLimiterConfig:
    max_concurrency: int = 2
    min_delay_seconds: float = 3.0
    max_delay_seconds: float = 9.0
    burst_limit_per_minute: int = 10
    max_errors_before_cooldown: int = 5
    cooldown_minutes: int = 30


@dataclass
class _SourceState:
    errors_in_window: int = 0
    window_start: float = field(default_factory=time.monotonic)
    cooldown_until: Optional[float] = None
    requests_this_minute: int = 0
    minute_start: float = field(default_factory=time.monotonic)


_DEFAULT_CONFIGS: dict[str, RateLimiterConfig] = {
    "zonaprop_api": RateLimiterConfig(
        max_concurrency=2,
        min_delay_seconds=3.0,
        max_delay_seconds=9.0,
        burst_limit_per_minute=10,
        max_errors_before_cooldown=5,
        cooldown_minutes=30,
    ),
    "zonaprop_browser": RateLimiterConfig(
        max_concurrency=1,
        min_delay_seconds=8.0,
        max_delay_seconds=20.0,
        burst_limit_per_minute=4,
        max_errors_before_cooldown=3,
        cooldown_minutes=60,
    ),
}


class RateLimiter:
    """
    Rate limiter por fuente. Mantiene estado de errores, cooldowns y
    semáforo de concurrencia. Thread-safe para asyncio (single-thread).
    """

    def __init__(self, source_key: str, config: Optional[RateLimiterConfig] = None) -> None:
        self.source_key = source_key
        self.config = config or _DEFAULT_CONFIGS.get(source_key) or RateLimiterConfig()
        self._sem = asyncio.Semaphore(self.config.max_concurrency)
        self._state = _SourceState()

    def is_in_cooldown(self) -> bool:
        if self._state.cooldown_until is None:
            return False
        if time.monotonic() >= self._state.cooldown_until:
            self._state.cooldown_until = None
            self._state.errors_in_window = 0
            return False
        return True

    def cooldown_remaining_seconds(self) -> float:
        if not self.is_in_cooldown():
            return 0.0
        return max(0.0, self._state.cooldown_until - time.monotonic())  # type: ignore[operator]

    def _enter_cooldown(self) -> None:
        cooldown_secs = self.config.cooldown_minutes * 60
        self._state.cooldown_until = time.monotonic() + cooldown_secs
        log.warning(
            "rate_limiter[%s]: entrando en cooldown por %d minutos",
            self.source_key,
            self.config.cooldown_minutes,
        )

    def record_error(self, http_status: Optional[int] = None) -> None:
        self._state.errors_in_window += 1
        if self._state.errors_in_window >= self.config.max_errors_before_cooldown:
            self._enter_cooldown()
        if http_status in (403, 429):
            log.warning(
                "rate_limiter[%s]: HTTP %d recibido — %d/%d errores",
                self.source_key,
                http_status,
                self._state.errors_in_window,
                self.config.max_errors_before_cooldown,
            )

    def record_success(self) -> None:
        self._state.errors_in_window = max(0, self._state.errors_in_window - 1)

    def _check_burst(self) -> None:
        now = time.monotonic()
        if now - self._state.minute_start >= 60.0:
            self._state.requests_this_minute = 0
            self._state.minute_start = now

    async def wait(self) -> None:
        """
        Espera el jitter entre requests. Lanza RuntimeError si la fuente está
        en cooldown para que el llamador pueda detenerse.
        """
        if self.is_in_cooldown():
            remaining = self.cooldown_remaining_seconds()
            raise CooldownError(
                f"[{self.source_key}] en cooldown por {remaining:.0f}s más"
            )

        self._check_burst()
        if self._state.requests_this_minute >= self.config.burst_limit_per_minute:
            wait_time = 60.0 - (time.monotonic() - self._state.minute_start)
            if wait_time > 0:
                log.info(
                    "rate_limiter[%s]: burst limit — esperando %.1fs",
                    self.source_key, wait_time,
                )
                await asyncio.sleep(wait_time)
            self._state.requests_this_minute = 0
            self._state.minute_start = time.monotonic()

        delay = random.uniform(self.config.min_delay_seconds, self.config.max_delay_seconds)
        log.debug("rate_limiter[%s]: delay=%.1fs", self.source_key, delay)
        await asyncio.sleep(delay)
        self._state.requests_this_minute += 1

    def semaphore(self) -> asyncio.Semaphore:
        return self._sem


class CooldownError(Exception):
    """Levantada cuando una fuente está en cooldown y no debe recibir requests."""


_limiters: dict[str, RateLimiter] = {}


def get_rate_limiter(source_key: str, config: Optional[RateLimiterConfig] = None) -> RateLimiter:
    if source_key not in _limiters:
        _limiters[source_key] = RateLimiter(source_key, config)
    return _limiters[source_key]
