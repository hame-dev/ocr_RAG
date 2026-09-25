"""Session login for the SPA.

Accounts are created by an operator (`manage.py create_user`); there is no
public sign-up endpoint by design.

CSRF: the frontend runs on a different origin than the API, so it cannot rely
on reading the csrftoken cookie. Every response that can change the token hands
it back in the JSON body and the client keeps it in memory.
"""
from __future__ import annotations

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from accounts.authentication import SessionAuthentication401


def _user_payload(user) -> dict:
    return {"id": user.id, "username": user.username, "is_staff": user.is_staff}


class LoginThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        # Key on client IP *and* username so one attacker cannot lock out every
        # account, and one account cannot be brute-forced from rotating IPs
        # faster than the per-IP limit allows.
        username = str(request.data.get("username", "")).lower()[:150]
        ident = f"{self.get_ident(request)}:{username}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=4096, trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(max_length=4096, trim_whitespace=False)
    new_password = serializers.CharField(max_length=4096, trim_whitespace=False)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def csrf(request):
    """Issue (and set the cookie for) a CSRF token."""
    return Response({"csrfToken": get_token(request)})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def login_view(request):
    # DRF skips CSRF for anonymous requests, but login CSRF (forcing a victim
    # into the attacker's account) is a real attack, so check it explicitly.
    SessionAuthentication401().enforce_csrf(request)

    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(
        request,
        username=serializer.validated_data["username"],
        password=serializer.validated_data["password"],
    )
    if user is None:
        # Deliberately generic: do not reveal whether the username exists.
        return Response(
            {"detail": "invalid username or password"}, status=status.HTTP_400_BAD_REQUEST
        )

    login(request, user)
    # login() rotated the CSRF token; return the new one.
    return Response({"user": _user_payload(user), "csrfToken": get_token(request)})



@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response({"user": _user_payload(request.user), "csrfToken": get_token(request)})


@api_view(["POST"])
def change_password(request):
    serializer = PasswordChangeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = request.user

    if not user.check_password(serializer.validated_data["old_password"]):
        return Response(
            {"old_password": ["current password is incorrect"]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    new_password = serializer.validated_data["new_password"]
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        return Response({"new_password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save(update_fields=["password"])
    # Keep this session alive; every other session for the user is invalidated.
    update_session_auth_hash(request, user)
    return Response({"csrfToken": get_token(request)})
