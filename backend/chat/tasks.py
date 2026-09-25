from __future__ import annotations

from celery import shared_task


@shared_task
def delete_stale_attachments() -> int:
    """Uploads that were never sent (tab closed, file removed) expire after a day."""
    from chat.attachments import delete_stale_unbound

    return delete_stale_unbound()
