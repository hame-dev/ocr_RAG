"""Upload size checks that run before Django reads the body.

A file's own size check runs only after the whole request has been spooled to
disk, so a multi-gigabyte upload is written out in full before it is refused.
Content-Length is known up front; checking it first refuses those immediately.
"""
from __future__ import annotations

# Room for the multipart boundaries and the other form fields.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def body_too_large(request, file_limit_bytes: int) -> bool:
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return False  # malformed: the parser and the file's own size check decide
    return length > file_limit_bytes + MULTIPART_OVERHEAD_BYTES
