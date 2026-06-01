"""Utilidades de saneamiento de datos externos antes de persistir en DB."""
from __future__ import annotations


def strip_nulls(value):
    """Elimina caracteres nulos (\x00) de strings — PostgreSQL UTF-8 los rechaza."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value
