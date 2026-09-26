"""Seed the default login so a fresh database can be signed into straight away.

    python manage.py seed_default_user

Runs as part of `make migrate` / the compose `migrate` service. Idempotent: an
existing account is left alone, so a password changed after seeding survives
every restart. Override the defaults with DEFAULT_USERNAME / DEFAULT_PASSWORD.
"""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEFAULT_USERNAME = "NCST_system"
DEFAULT_PASSWORD = "NCST@12345"


class Command(BaseCommand):
    help = "Create the default staff login if it does not exist yet."

    def handle(self, *args, **options):
        username = os.environ.get("DEFAULT_USERNAME") or DEFAULT_USERNAME
        password = os.environ.get("DEFAULT_PASSWORD") or DEFAULT_PASSWORD

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"default user {username!r} already exists, leaving it unchanged")
            return

        # A known default on purpose, so the password validators are not applied.
        User.objects.create_superuser(username=username, email="", password=password)
        self.stdout.write(self.style.SUCCESS(f"created default user {username!r}"))
