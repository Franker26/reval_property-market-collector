"""
Sistema de alertas desacoplado para el pipeline de discovery.

Diseño: pub-sub con handlers registrables. El dispatcher es un singleton
inicializado en el lifespan de la app. Los handlers se disparan de forma
fire-and-forget (asyncio.create_task) para no bloquear el pipeline.

Handlers incluidos:
  - LogAlertHandler: siempre activo, log.warning/error según severity
  - TelegramAlertHandler: activo si TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID en env
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    event_type: str
    severity: str          # "warning" | "critical"
    message: str
    metadata: dict = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@runtime_checkable
class AlertHandler(Protocol):
    async def send(self, event: AlertEvent) -> None: ...


class LogAlertHandler:
    async def send(self, event: AlertEvent) -> None:
        level = logging.ERROR if event.severity == "critical" else logging.WARNING
        log.log(
            level,
            "ALERT[%s/%s]: %s | meta=%s",
            event.event_type,
            event.severity,
            event.message,
            event.metadata,
        )


class TelegramAlertHandler:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    async def send(self, event: AlertEvent) -> None:
        severity_emoji = "🔴" if event.severity == "critical" else "⚠️"
        lines = [
            f"{severity_emoji} *{event.event_type.upper()}*",
            event.message,
        ]
        if event.metadata:
            meta_str = " | ".join(f"{k}: {v}" for k, v in event.metadata.items())
            lines.append(f"_{meta_str}_")
        lines.append(f"🕐 {event.occurred_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        text = "\n".join(lines)

        try:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession() as session:
                await session.post(
                    self._url,
                    json={"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
        except Exception as exc:
            log.warning("TelegramAlertHandler: fallo al enviar alerta — %s", exc)


class AlertDispatcher:
    def __init__(self) -> None:
        self._handlers: list[AlertHandler] = []

    def register(self, handler: AlertHandler) -> None:
        self._handlers.append(handler)

    async def dispatch(self, event: AlertEvent) -> None:
        for handler in self._handlers:
            try:
                asyncio.create_task(handler.send(event))
            except Exception as exc:
                log.warning("AlertDispatcher: error disparando handler %s — %s", handler, exc)


_dispatcher: AlertDispatcher | None = None


def get_dispatcher() -> AlertDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher()
    return _dispatcher


def setup_alert_dispatcher() -> None:
    """
    Inicializa el dispatcher y registra los handlers configurados via env vars.
    Llamar en el lifespan de la app, después de inicializar el scheduler.
    """
    dispatcher = get_dispatcher()
    dispatcher.register(LogAlertHandler())

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        dispatcher.register(TelegramAlertHandler(token, chat_id))
        log.info("alerts: Telegram handler registrado (chat_id=%s)", chat_id)
    else:
        log.info("alerts: solo LogAlertHandler activo (TELEGRAM_BOT_TOKEN/CHAT_ID no configurados)")


async def dispatch(
    event_type: str,
    severity: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    """Shortcut para disparar una alerta desde cualquier parte del código."""
    event = AlertEvent(
        event_type=event_type,
        severity=severity,
        message=message,
        metadata=metadata or {},
    )
    await get_dispatcher().dispatch(event)
