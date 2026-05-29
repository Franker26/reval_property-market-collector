"""GET /logs — consulta el ring buffer de logs en memoria."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.core.log_buffer import _MAX_ENTRIES, get_entries

router = APIRouter(tags=["logs"])

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.get("/logs")
def get_logs(
    limit: int = Query(default=200, ge=1, le=_MAX_ENTRIES),
    level: Optional[str] = Query(default=None, description="DEBUG | INFO | WARNING | ERROR"),
    logger: Optional[str] = Query(default=None, description="Prefijo del logger, ej: 'scheduler', 'app.repositories'"),
):
    """
    Retorna los logs más recientes capturados en memoria.

    - **limit**: cuántas entradas devolver (default 200, max 2000)
    - **level**: filtrar por nivel exacto
    - **logger**: filtrar por prefijo del nombre del logger
    """
    if level and level.upper() not in _VALID_LEVELS:
        from fastapi import HTTPException
        raise HTTPException(400, f"level inválido: {level!r}. Usar uno de {sorted(_VALID_LEVELS)}")

    entries, total_buffered = get_entries(
        limit=limit,
        level=level or None,
        logger_prefix=logger or None,
    )

    return {
        "entries": entries,
        "count": len(entries),
        "buffer_size": _MAX_ENTRIES,
        "total_buffered": total_buffered,
    }
