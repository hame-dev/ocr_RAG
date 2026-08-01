"""ASGI entrypoint.

Django is served through ASGI (not WSGI) because the SSE endpoints — document
progress and chat token streaming — are async views. DRF views stay sync and run
in the ASGI threadpool; do not convert the SSE views into DRF APIViews.

The lifespan hook owns the psycopg pool used by the LangGraph checkpointer. That
pool is deliberately separate from Django's ORM connections: mixing an async
pool with Django's connection handling produces "connection already closed"
failures that are very hard to trace.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from django.core.asgi import get_asgi_application  # noqa: E402

django_application = get_asgi_application()


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    from chat.checkpointer import open_pool

                    await open_pool()
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:  # pragma: no cover - startup diagnostics
                    await send(
                        {"type": "lifespan.startup.failed", "message": str(exc)}
                    )
            elif message["type"] == "lifespan.shutdown":
                try:
                    from chat.checkpointer import close_pool

                    await close_pool()
                finally:
                    await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await django_application(scope, receive, send)
