"""Engine registry + health probing.

`GET /api/ocr/engines/` returns the probed catalog and the frontend only offers
engines reporting available=True. Probes are real calls (does Tesseract actually
have the Arabic language pack? is the model actually pulled?), because an engine
that imports fine but has no Arabic data would otherwise be offered and then
return garbage.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from django.conf import settings

from .base import EngineHealth, OCREngine

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[OCREngine]] = {}
_HEALTH_CACHE_KEY = "ocr:health:v1"
_PROBE_TIMEOUT_S = 6.0


def register(cls: type[OCREngine]) -> type[OCREngine]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a name")
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> OCREngine:
    if name not in _REGISTRY:
        raise KeyError(f"unknown OCR engine {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def names() -> list[str]:
    return sorted(_REGISTRY)


def all_engines() -> list[OCREngine]:
    return [cls() for cls in _REGISTRY.values()]


def _probe(engine: OCREngine) -> EngineHealth:
    started = time.monotonic()
    try:
        health = engine.health()
    except Exception as exc:
        logger.debug("health probe failed for %s", engine.name, exc_info=True)
        health = EngineHealth(available=False, detail=f"{type(exc).__name__}: {exc}")
    if health.latency_ms is None:
        health.latency_ms = int((time.monotonic() - started) * 1000)
    return health


def probe_all(force: bool = False) -> list[dict]:
    """Probe every engine in parallel, cached in Redis for ENGINE_HEALTH_TTL_S."""
    from common.fsm import get_redis

    redis_client = get_redis()

    if not force:
        try:
            cached = redis_client.get(_HEALTH_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.debug("health cache read failed", exc_info=True)

    engines = all_engines()
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(len(engines), 1)) as pool:
        futures = {pool.submit(_probe, e): e for e in engines}
        for future, engine in futures.items():
            try:
                health = future.result(timeout=_PROBE_TIMEOUT_S)
            except FuturesTimeout:
                health = EngineHealth(
                    available=False, detail=f"health probe timed out after {_PROBE_TIMEOUT_S}s"
                )
            except Exception as exc:
                health = EngineHealth(available=False, detail=str(exc))
            results.append(engine.catalog_entry(health))

    # Available first, then by tier, then by speed — so the UI's default
    # ordering already suggests sensible picks.
    tier_order = {"native": 0, "classical": 1, "neural": 2, "vlm": 3}
    results.sort(
        key=lambda e: (
            not e["available"],
            tier_order.get(e["tier"], 9),
            e["est_seconds_per_page"],
        )
    )

    try:
        redis_client.setex(
            _HEALTH_CACHE_KEY, settings.ENGINE_HEALTH_TTL_S, json.dumps(results)
        )
    except Exception:
        logger.debug("health cache write failed", exc_info=True)

    return results


def available_names() -> list[str]:
    return [e["name"] for e in probe_all() if e["available"]]


def load_engines() -> None:
    """Import every engine module so decorators run. Called from apps.ready()."""
    from . import (  # noqa: F401
        easyocr_remote,
        native_pdf,
        surya,
        tesseract,
        vlm_ollama,
    )
