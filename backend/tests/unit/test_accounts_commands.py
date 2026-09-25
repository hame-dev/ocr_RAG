from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from chat.models import Conversation
from documents.models import Document

pytestmark = pytest.mark.django_db


def test_create_user_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-passphrase\n"))

    call_command("create_user", "carol", "--staff", "--password-stdin", stdout=io.StringIO())

    user = get_user_model().objects.get(username="carol")
    assert user.is_staff
    assert user.check_password("a-long-enough-passphrase")


def test_create_user_rejects_weak_passwords_and_duplicates(monkeypatch, user):
    monkeypatch.setattr("sys.stdin", io.StringIO("123\n"))
    with pytest.raises(CommandError):
        call_command("create_user", "dave", "--password-stdin")
    assert not get_user_model().objects.filter(username="dave").exists()

    monkeypatch.setattr("sys.stdin", io.StringIO("a-long-enough-passphrase\n"))
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_user", "alice", "--password-stdin")


def test_claim_unowned_assigns_only_rows_without_an_owner(user, other_user):
    orphan = Document.objects.create(
        original_filename="old.pdf", mime_type="application/pdf", storage_path="/tmp/old.pdf"
    )
    theirs = Document.objects.create(
        owner=other_user,
        original_filename="bob.pdf",
        mime_type="application/pdf",
        storage_path="/tmp/bob.pdf",
    )
    orphan_chat = Conversation.objects.create()

    call_command("claim_unowned", "alice", stdout=io.StringIO())

    orphan.refresh_from_db()
    theirs.refresh_from_db()
    orphan_chat.refresh_from_db()
    assert orphan.owner == user
    assert theirs.owner == other_user
    assert orphan_chat.owner == user
