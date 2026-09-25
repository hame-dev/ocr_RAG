"""Assign documents and conversations created before login existed to a user.

Rows without an owner are invisible to everyone, so an upgraded install shows an
empty library until this runs once:

    python manage.py claim_unowned alice
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chat.models import Conversation
from documents.models import Document


class Command(BaseCommand):
    help = "Give every document and conversation without an owner to USERNAME."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, username: str, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"no such user {username!r}")

        documents = Document.objects.filter(owner__isnull=True).update(owner=user)
        conversations = Conversation.objects.filter(owner__isnull=True).update(owner=user)
        self.stdout.write(
            self.style.SUCCESS(
                f"assigned {documents} document(s) and {conversations} conversation(s) "
                f"to {username!r}"
            )
        )
