from __future__ import annotations

import os
import shutil

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from chat.models import ChatAttachment


@receiver(post_delete, sender=ChatAttachment)
def remove_attachment_files(sender, instance: ChatAttachment, **kwargs):
    # Rows also disappear by cascade when a conversation or user is deleted;
    # this keeps the files on disk in step.
    shutil.rmtree(os.path.join(settings.MEDIA_ROOT, "chat", str(instance.id)), ignore_errors=True)
