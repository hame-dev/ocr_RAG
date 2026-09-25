"""Create a login. There is no public sign-up; this is how accounts are made.

    python manage.py create_user alice
    python manage.py create_user admin --staff
    echo "$PASSWORD" | python manage.py create_user ci-bot --password-stdin
"""
from __future__ import annotations

import getpass
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create a user account (accounts are operator-created; there is no sign-up)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--staff", action="store_true", help="Mark the user as staff.")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from stdin instead of prompting (for scripts).",
        )

    def handle(self, *args, username: str, staff: bool, password_stdin: bool, **options):
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise CommandError(f"user {username!r} already exists")

        if password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("passwords do not match")
        if not password:
            raise CommandError("password must not be empty")

        user = User(username=username, is_staff=staff)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"created user {username!r}"))
