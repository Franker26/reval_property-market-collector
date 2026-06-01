from __future__ import annotations

from fastapi import Header, HTTPException

from app.core.config import get_settings


async def require_api_key(x_reval_mi_key: str = Header(default="")) -> None:
    """
    Valida el header X-Reval-MI-Key contra REVAL_MI_API_KEY.

    En desarrollo sin key configurada: permite el acceso (conveniencia local).
    En producción sin key configurada: rechaza (misconfiguration explícita).
    Con key configurada: exige coincidencia exacta en cualquier entorno.
    """
    settings = get_settings()
    key = settings.reval_mi_api_key
    if not key:
        if settings.app_env == "production":
            raise HTTPException(status_code=403, detail="API key not configured on server")
        return
    if x_reval_mi_key != key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
