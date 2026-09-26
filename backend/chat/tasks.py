from __future__ import annotations

from celery import shared_task


@shared_task
def delete_stale_attachments() -> int:
    """Uploads that were never sent (tab closed, file removed) expire after a day,
    as do generated files whose turn was abandoned before it finished."""
    from chat.attachments import delete_stale_unbound
    from chat.code_tools import delete_stale_unbound_files

    return delete_stale_unbound() + delete_stale_unbound_files()
