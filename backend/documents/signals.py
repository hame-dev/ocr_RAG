from __future__ import annotations

import shutil

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from documents.models import Document


@receiver(post_delete, sender=Document)
def remove_document_files(sender, instance: Document, **kwargs):
    # The original upload, every raster profile and the thumbnail all live
    # under this one directory. Removed only once the delete has committed, so
    # a rolled-back delete never leaves a row pointing at missing files. Also
    # fires on cascade from a deleted user.
    from documents.services.preprocess import doc_dir

    directory = doc_dir(instance)
    transaction.on_commit(lambda: shutil.rmtree(directory, ignore_errors=True))
