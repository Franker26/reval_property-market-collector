"""Fábrica de sesiones async y sync para SQLAlchemy."""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings


def _get_async_engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


def _get_sync_engine():
    settings = get_settings()
    return create_engine(settings.sync_database_url, echo=False, future=True)


# Lazy singletons — no se crean hasta que se llaman por primera vez
_async_engine = None
_async_session_factory = None
_sync_engine = None
_sync_session_factory = None


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        _async_engine = _get_async_engine()
    return _async_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_async_engine(), expire_on_commit=False
        )
    return _async_session_factory


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = _get_sync_engine()
    return _sync_engine


def get_sync_session_factory() -> sessionmaker[Session]:
    global _sync_session_factory
    if _sync_session_factory is None:
        _sync_session_factory = sessionmaker(get_sync_engine(), expire_on_commit=False)
    return _sync_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency para sesión async."""
    factory = get_async_session_factory()
    async with factory() as session:
        yield session
