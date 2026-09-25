from __future__ import annotations

import json

import pytest
from django.test import Client

from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


def _csrf_client() -> tuple[Client, str]:
    """A client that enforces CSRF like a real browser, plus a valid token."""
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/auth/csrf/").json()["csrfToken"]
    return client, token


def _login(client: Client, token: str, username: str, password: str):
    return client.post(
        "/api/auth/login/",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )


def test_login_sets_a_session_and_me_returns_the_user(user):
    client, token = _csrf_client()

    response = _login(client, token, "alice", PASSWORD)

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["username"] == "alice"
    # login() rotates the token; the response must carry the new one.
    assert payload["csrfToken"] and payload["csrfToken"] != token
    me = client.get("/api/auth/me/")
    assert me.status_code == 200
    assert me.json()["user"] == {"id": user.id, "username": "alice", "is_staff": False}


def test_wrong_password_is_rejected_generically(user):
    client, token = _csrf_client()

    wrong_password = _login(client, token, "alice", "nope")
    unknown_user = _login(client, token, "nobody", "nope")

    assert wrong_password.status_code == unknown_user.status_code == 400
    assert wrong_password.json() == unknown_user.json()
    assert client.get("/api/auth/me/").status_code == 401


def test_login_requires_a_csrf_token(user):
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        "/api/auth/login/",
        data=json.dumps({"username": "alice", "password": PASSWORD}),
        content_type="application/json",
    )

    assert response.status_code == 403


def test_login_is_throttled(user):
    client, token = _csrf_client()

    statuses = [_login(client, token, "alice", "wrong").status_code for _ in range(6)]

    assert statuses[:5] == [400] * 5
    assert statuses[5] == 429


def test_logout_ends_the_session(user):
    client, token = _csrf_client()
    token = _login(client, token, "alice", PASSWORD).json()["csrfToken"]

    response = client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 204
    assert client.get("/api/auth/me/").status_code == 401


def test_authenticated_writes_still_require_csrf(user):
    client, token = _csrf_client()
    _login(client, token, "alice", PASSWORD)

    response = client.post(
        "/api/conversations/",
        data=json.dumps({"scope": "all"}),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/documents/"),
        ("get", "/api/conversations/"),
        ("get", "/api/ocr/engines/"),
        ("post", "/api/search/"),
        ("get", "/api/documents/00000000-0000-0000-0000-000000000001/events/"),
        ("post", "/api/conversations/00000000-0000-0000-0000-000000000001/stream/"),
    ],
)
def test_anonymous_callers_get_401(client, method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 401
    assert "WWW-Authenticate" in response


def test_change_password(user, auth_client):
    response = auth_client.post(
        "/api/auth/password/",
        data=json.dumps({"old_password": PASSWORD, "new_password": "a-much-better-passphrase"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("a-much-better-passphrase")
    # update_session_auth_hash keeps the current session valid.
    assert auth_client.get("/api/auth/me/").status_code == 200


def test_change_password_checks_old_password_and_validators(user, auth_client):
    wrong_old = auth_client.post(
        "/api/auth/password/",
        data=json.dumps({"old_password": "nope", "new_password": "a-much-better-passphrase"}),
        content_type="application/json",
    )
    too_weak = auth_client.post(
        "/api/auth/password/",
        data=json.dumps({"old_password": PASSWORD, "new_password": "123"}),
        content_type="application/json",
    )

    assert wrong_old.status_code == 400
    assert "old_password" in wrong_old.json()
    assert too_weak.status_code == 400
    assert "new_password" in too_weak.json()
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
