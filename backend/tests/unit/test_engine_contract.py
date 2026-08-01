from ocr.engines import registry
from ocr.engines.base import EngineHealth, OCREngine
from ocr.tasks import _engine_signatures


def test_every_registered_engine_uses_shared_contract():
    engines = registry.all_engines()

    assert len(engines) == 8
    for engine in engines:
        assert isinstance(engine, OCREngine)
        entry = engine.catalog_entry(EngineHealth(available=True))
        assert entry["name"] == engine.name
        assert entry["available"] is True
        assert entry["queue"] in {"ocr_cpu", "llm"}


def test_chord_headers_are_immutable_and_routed_per_engine():
    signatures = _engine_signatures("batch-id", ["tesseract", "vlm_qwen35"])

    assert [signature.immutable for signature in signatures] == [True, True]
    assert [signature.options["queue"] for signature in signatures] == [
        "ocr_cpu",
        "llm",
    ]
