"""Ring buffer de logs en memoria para consulta vía API."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

_MAX_ENTRIES = 2000
_buffer: deque[dict] = deque(maxlen=_MAX_ENTRIES)

_FMT = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "time": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": _FMT.format(record),
            })
        except Exception:
            pass


_handler: _RingBufferHandler | None = None


def setup_log_buffer() -> None:
    global _handler
    if _handler is not None:
        return
    _handler = _RingBufferHandler()
    _handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(_handler)


def get_entries(
    limit: int = 200,
    level: str | None = None,
    logger_prefix: str | None = None,
) -> tuple[list[dict], int]:
    """Devuelve (entradas filtradas más recientes, total en buffer)."""
    total = len(_buffer)
    entries: list[dict] = list(_buffer)
    if level:
        entries = [e for e in entries if e["level"] == level.upper()]
    if logger_prefix:
        entries = [e for e in entries if e["logger"].startswith(logger_prefix)]
    return entries[-limit:], total
