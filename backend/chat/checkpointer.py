"""LangGraph Postgres checkpointer — the agent's memory.

The pool is opened in the ASGI lifespan hook and kept deliberately SEPARATE from
Django's ORM connections. Sharing them (or letting Django manage this pool)
produces intermittent "connection already closed" errors that are extremely hard
to trace back to their cause.
"""
from __future__ import annotations

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

_pool = None
_checkpointer = None


def _dsn() -> str:
    db = settings.DATABASES["default"]
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT'] or 5432}/{db['NAME']}"
        "?sslmode=disable"
    )


async def open_pool():
    """Called once from the ASGI lifespan startup hook."""
    global _pool, _checkpointer
    if _pool is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        logger.warning("langgraph postgres checkpointer unavailable; memory disabled")
        return None

    _pool = AsyncConnectionPool(
        conninfo=_dsn(),
        max_size=8,
        min_size=1,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        # Validate on checkout: after a Postgres restart the pool otherwise
        # hands a dead connection to the next chat turn, which then fails with
        # "terminating connection due to administrator command".
        check=AsyncConnectionPool.check_connection,
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(_pool)
    logger.info("langgraph checkpointer pool opened")
    return _checkpointer


async def close_pool():
    global _pool, _checkpointer
    if _pool is not None:
        await _pool.close()
        _pool = None
        _checkpointer = None
        logger.info("langgraph checkpointer pool closed")


def get_checkpointer():
    """The live checkpointer, or None if memory is unavailable.

    A missing checkpointer degrades to a stateless agent rather than a 500 —
    the user still gets answers, they just do not carry across turns.
    """
    return _checkpointer


def setup_sync():
    """One-time table creation. Run via `manage.py init_checkpointer`."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_dsn()) as saver:
        saver.setup()
    return True
