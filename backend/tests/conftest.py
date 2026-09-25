from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _clear_cache():
    # Login throttle counters live in the cache; never let them leak between tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="alice", password=PASSWORD)


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="bob", password=PASSWORD)


@pytest.fixture
def auth_client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def other_client(other_user) -> Client:
    client = Client()
    client.force_login(other_user)
    return client
