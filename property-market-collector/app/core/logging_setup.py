"""Configura el root logger con formato consistente para toda la app."""
from __future__ import annotations

import logging
import sys


def configure_root_logger(level: int = logging.INFO) -> None:
    """
    Aplica formato uniforme a todos los handlers existentes del root logger,
    o agrega un StreamHandler si no hay ninguno aún.

    Llamar antes de que uvicorn configure el suyo, o justo después — en ambos
    casos el formatter se aplica a todos los handlers activos.
    """
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        for h in root.handlers:
            if not isinstance(h, logging.NullHandler):
                h.setFormatter(fmt)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        root.addHandler(handler)
