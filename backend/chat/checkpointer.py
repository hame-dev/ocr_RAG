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


# LangGraph's tables, all keyed by thread_id (== Conversation.id).
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def delete_thread(thread_id: str) -> None:
    """Erase a conversation's agent memory.

    Deleting the Conversation row alone would leave the whole chat, every
    message and tool result, in these tables. Plain SQL over Django's own
    connection, so it commits or rolls back with the row's deletion.
    """
    from django.db import connection

    existing = set(connection.introspection.table_names())
    with connection.cursor() as cursor:
        for table in CHECKPOINT_TABLES:
            if table in existing:  # absent until init_checkpointer has run (e.g. tests)
                cursor.execute(f"DELETE FROM {table} WHERE thread_id = %s", [thread_id])


# Keep only the newest checkpoint per namespace, then the writes and blobs it
# still references. LangGraph otherwise keeps every super-step of every turn,
# and each one re-stores the message list (base64 images included).
# Checkpoint ids are uuid6, so text order is time order.
_PRUNE_SQL = (
    """
    DELETE FROM checkpoints c
    USING (
        SELECT DISTINCT ON (checkpoint_ns) checkpoint_ns, checkpoint_id
        FROM checkpoints WHERE thread_id = %(t)s
        ORDER BY checkpoint_ns, checkpoint_id DESC
    ) latest
    WHERE c.thread_id = %(t)s
      AND c.checkpoint_ns = latest.checkpoint_ns
      AND c.checkpoint_id <> latest.checkpoint_id
    """,
    """
    DELETE FROM checkpoint_writes w
    WHERE w.thread_id = %(t)s AND NOT EXISTS (
        SELECT 1 FROM checkpoints c
        WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns
          AND c.checkpoint_id = w.checkpoint_id
    )
    """,
    """
    DELETE FROM checkpoint_blobs b
    WHERE b.thread_id = %(t)s AND NOT EXISTS (
        SELECT 1 FROM checkpoints c
        WHERE c.thread_id = b.thread_id AND c.checkpoint_ns = b.checkpoint_ns
          AND c.checkpoint -> 'channel_versions' ->> b.channel = b.version
    )
    """,
)


async def prune_thread(thread_id: str) -> None:
    """Drop a conversation's superseded checkpoints. Never raises.

    Run after a turn has finished, when no run on this thread is in flight
    (the per-conversation turn lock guarantees that).
    """
    if _pool is None:
        return
    try:
        async with _pool.connection() as conn:
            async with conn.transaction():
                for sql in _PRUNE_SQL:
                    await conn.execute(sql, {"t": str(thread_id)})
    except Exception:
        logger.warning("could not prune checkpoints for %s", thread_id, exc_info=True)


def setup_sync():
    """One-time table creation. Run via `manage.py init_checkpointer`."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(_dsn()) as saver:
        saver.setup()
    return True
