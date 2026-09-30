from __future__ import annotations

import os
import shutil

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from chat.models import ChatAttachment, Conversation, GeneratedFile


@receiver(post_delete, sender=Conversation)
def remove_agent_memory(sender, instance: Conversation, **kwargs):
    # Runs in the deleting transaction, so memory and row go together. Also
    # fires on cascade from a deleted user, which the API view never sees.
    from chat.checkpointer import delete_thread

    delete_thread(instance.thread_id)


@receiver(post_delete, sender=ChatAttachment)
def remove_attachment_files(sender, instance: ChatAttachment, **kwargs):
    # Rows also disappear by cascade when a conversation or user is deleted;
    # this keeps the files on disk in step.
    shutil.rmtree(os.path.join(settings.MEDIA_ROOT, "chat", str(instance.id)), ignore_errors=True)


@receiver(post_delete, sender=GeneratedFile)
def remove_generated_files(sender, instance: GeneratedFile, **kwargs):
    from chat.code_tools import generated_dir

    shutil.rmtree(generated_dir(instance.id), ignore_errors=True)
