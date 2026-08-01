"""Raster profiles.

Engines share cached rasters so a multi-engine run rasterizes once per profile
rather than once per engine.

The split matters for accuracy, not just speed: classical engines (Tesseract)
want high-DPI binarized input, while neural and VLM engines are trained on
natural grayscale/colour images and are measurably HURT by binarization.
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    # Tesseract: high DPI, grayscale, Sauvola-binarized.
    "classical": {
        "dpi": 300,
        "grayscale": True,
        "deskew": True,
        "denoise": "light",
        "binarize": "sauvola",
        "max_long_edge": 4200,
    },
    # Surya / EasyOCR / Paddle: moderate DPI, colour, no binarization.
    "neural": {
        "dpi": 200,
        "grayscale": False,
        "deskew": True,
        "denoise": "none",
        "binarize": None,
        "max_long_edge": 2600,
    },
    # VLMs: lower DPI because every pixel costs tokens.
    "vlm": {
        "dpi": 150,
        "grayscale": False,
        "deskew": True,
        "denoise": "none",
        "binarize": None,
        "max_long_edge": 1800,
    },
    # Escape hatch for debugging: exactly what was rendered, nothing applied.
    "raw": {
        "dpi": 200,
        "grayscale": False,
        "deskew": False,
        "denoise": "none",
        "binarize": None,
        "max_long_edge": 3000,
    },
}


def profile(name: str) -> dict:
    return PROFILES.get(name, PROFILES["neural"])


def raster_key(profile_name: str, dpi: int) -> str:
    return f"{profile_name}@{dpi}"
