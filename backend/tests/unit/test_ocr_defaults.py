from django.conf import settings

from ocr.serializers import StartOCRSerializer


def test_chandra_ollama_is_the_default_ocr_engine():
    assert settings.DEFAULT_OCR_ENGINE == "chandra_ollama"


def test_engine_catalog_exposes_the_default(monkeypatch, client):
    monkeypatch.setattr("ocr.engines.registry.probe_all", lambda force=False: [])

    response = client.get("/api/ocr/engines/")

    assert response.status_code == 200
    assert response.json() == {"engines": [], "default_engine": "chandra_ollama"}


def test_start_ocr_request_can_omit_engines_and_use_the_backend_default():
    serializer = StartOCRSerializer(data={})

    assert serializer.is_valid(), serializer.errors
    assert "engines" not in serializer.validated_data
