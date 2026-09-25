from __future__ import annotations

from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """Session auth that answers anonymous callers with 401, not 403.

    DRF only returns 401 when the first authenticator supplies a
    WWW-Authenticate challenge; plain SessionAuthentication does not, so every
    "not logged in" would surface as 403 and be indistinguishable from
    "logged in but not allowed". The frontend redirects to /login on 401 only.
    """

    def authenticate_header(self, request) -> str:
        return 'Session realm="api"'
