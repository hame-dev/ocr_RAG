"""Health endpoints.

`/api/health/` is the container liveness probe and must stay trivially fast.
`/api/health/deep/` is the operator view: it tells you which dependency is
actually broken, including per-engine status.

Both are public: container healthchecks and the smoke script call them without
a session. Anonymous callers of the deep check get only ok/degraded per
dependency; the details (hosts, model names, raw errors) need a login.
"""
from __future__ import annotations

from django.conf import settings
from django.db import connection
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.authentication import SessionAuthentication401


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def liveness(request):
    return Response({"status": "ok"})


@api_view(["GET"])
@authentication_classes([SessionAuthentication401])
@permission_classes([AllowAny])
def readiness(request):
    checks: dict[str, dict] = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.execute("SELECT extname FROM pg_extension")
            extensions = {row[0] for row in cursor.fetchall()}
        missing = {"vector", "pg_trgm"} - extensions
        checks["database"] = {
            "ok": not missing,
            "extensions": sorted(extensions & {"vector", "pg_trgm"}),
            "detail": f"missing extensions: {sorted(missing)}" if missing else "",
        }
        ok &= not missing
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": str(exc)}
        ok = False

    try:
        from common.fsm import get_redis

        get_redis().ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:
        checks["redis"] = {"ok": False, "detail": str(exc)}
        ok = False

    try:
        from common.ollama import get_client

        client = get_client()
        names = {m.get("name", "") for m in client.tags()}
        required = {settings.LLM_MODEL, settings.EMBED_MODEL}
        present = {
            model
            for model in required
            if model in names or any(n.split(":")[0] == model.split(":")[0] for n in names)
        }
        missing = required - present
        checks["ollama"] = {
            "ok": not missing,
            "base_url": client.base_url,
            "models_present": sorted(present),
            "detail": (
                f"missing models: {sorted(missing)} — run scripts/warmup_models.sh"
                if missing else ""
            ),
        }
        ok &= not missing
    except Exception as exc:
        checks["ollama"] = {
            "ok": False,
            "base_url": settings.OLLAMA_BASE_URL,
            "detail": f"{exc} (is Ollama running on the host?)",
        }
        ok = False

    try:
        from ocr.engines import registry

        engines = registry.probe_all()
        checks["ocr_engines"] = {
            # At least one working engine is enough for the product to function.
            "ok": any(e["available"] for e in engines),
            "available": [e["name"] for e in engines if e["available"]],
            "unavailable": {
                e["name"]: e.get("detail", "") for e in engines if not e["available"]
            },
        }
        ok &= checks["ocr_engines"]["ok"]
    except Exception as exc:
        checks["ocr_engines"] = {"ok": False, "detail": str(exc)}
        ok = False

    if not request.user.is_authenticated:
        checks = {name: {"ok": check["ok"]} for name, check in checks.items()}
    return Response({"status": "ok" if ok else "degraded", "checks": checks},
                    status=200 if ok else 503)
