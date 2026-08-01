"""Server-sent events helpers.

SSE carries both OCR progress and chat tokens. It is plain HTTP/1.1, so it
survives `curl`, reconnects on its own, and `Last-Event-ID` gives free gap-free
resume — which matters because a multi-engine OCR batch runs for minutes and the
user will switch tabs.
"""
from __future__ import annotations

import json

HEARTBEAT = ": ping\n\n"


def sse(event: str, data: dict | list | str, id: int | str | None = None) -> str:
    """Format one SSE frame."""
    parts = []
    if id is not None:
        parts.append(f"id: {id}")
    parts.append(f"event: {event}")
    body = data if isinstance(data, str) else json.dumps(data, default=str)
    # A data field cannot contain raw newlines; each line needs its own prefix.
    for line in body.split("\n"):
        parts.append(f"data: {line}")
    return "\n".join(parts) + "\n\n"


def stream_headers(response):
    """Apply the headers that keep a stream unbuffered end to end."""
    response["Cache-Control"] = "no-cache"
    response["Connection"] = "keep-alive"
    # Tells nginx-family proxies not to buffer. Without this the stream arrives
    # as one blob at the end and chat feels broken.
    response["X-Accel-Buffering"] = "no"
    response["Content-Encoding"] = "identity"
    return response
