"""Image preprocessing.

Two Arabic-specific rules in here prevent whole classes of "the OCR is weirdly
wrong" bugs. Both are load-bearing; read the comments before changing anything.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def estimate_skew(img: np.ndarray, max_angle: float = 15.0) -> float:
    """Estimate page skew in degrees (positive = counter-clockwise correction).

    Deskew is the single highest-value preprocessing step for Arabic. Arabic
    script sits on a continuous connected baseline, so even 1-2 degrees of skew
    breaks the connected-component line and word segmentation that Tesseract and
    Paddle's detector depend on. Latin script tolerates skew far better.
    """
    gray = to_gray(img)

    # Work on a downsampled copy: the angle is a global property and this keeps
    # the estimate fast on 300dpi pages.
    scale = 1000.0 / max(gray.shape)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    # Invert so text is white, then blur horizontally to merge glyphs of a line
    # into a single blob whose orientation is the line's orientation.
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
    dilated = cv2.dilate(thresh, kernel, iterations=1)

    coords = cv2.findNonZero(dilated)
    if coords is None or len(coords) < 50:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]
    # minAreaRect reports in (-90, 0]; normalize to a small correction angle.
    if angle < -45:
        angle = 90 + angle
    if angle > 45:
        angle = angle - 90

    if abs(angle) > max_angle:
        # Almost certainly a mis-estimate (e.g. a page dominated by a figure).
        return 0.0
    return float(angle)


def deskew(img: np.ndarray, angle: float | None = None,
           min_angle: float = 0.3) -> tuple[np.ndarray, float]:
    """Rotate to correct skew. Below `min_angle` the rotation costs more in
    resampling blur than it recovers in accuracy."""
    if angle is None:
        angle = estimate_skew(img)
    if abs(angle) < min_angle:
        return img, 0.0

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    border = 255 if img.ndim == 2 else (255, 255, 255)
    rotated = cv2.warpAffine(
        img, matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )
    return rotated, float(angle)


def denoise(img: np.ndarray, mode: str = "light") -> np.ndarray:
    """Remove scan speckle.

    HARD RULE: never apply morphological opening or erosion to Arabic.

    Arabic distinguishes ب/ت/ث, ج/ح/خ, ر/ز, س/ش, ص/ض, ط/ظ, ع/غ purely by i'jam
    dots that are 1-3 pixels at 300dpi. Opening deletes those dots and silently
    converts one letter into another — the OCR then confidently returns a real
    but wrong word, which is far worse than a garbled one. A median filter
    preserves them.
    """
    if mode == "none":
        return img
    if mode == "light":
        return cv2.medianBlur(img, 3)
    if mode == "medium":
        return cv2.medianBlur(img, 5)
    logger.warning("unknown denoise mode %r; skipping", mode)
    return img


def binarize(img: np.ndarray, method: str | None = "sauvola") -> np.ndarray:
    """Binarize for classical OCR only.

    Sauvola is a local threshold, so it survives the uneven illumination typical
    of phone photos and flatbed scans, where global Otsu blows out one side of
    the page.

    Do NOT call this for neural/VLM engines — they are trained on natural
    images and binarization measurably degrades them.
    """
    if not method:
        return img

    gray = to_gray(img)
    if method == "otsu":
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    if method == "sauvola":
        try:
            from skimage.filters import threshold_sauvola

            thresh = threshold_sauvola(gray, window_size=25, k=0.2)
            return ((gray > thresh) * 255).astype(np.uint8)
        except Exception:
            logger.warning("sauvola failed; falling back to otsu", exc_info=True)
            return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    logger.warning("unknown binarize method %r; skipping", method)
    return img


def cap_long_edge(img: np.ndarray, max_long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_long_edge:
        return img
    scale = max_long_edge / longest
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)


def detect_orientation(image_path: str) -> int:
    """Detect 0/90/180/270 rotation using Tesseract OSD.

    Cheap, and it catches sideways scans that would otherwise make every engine
    return garbage.
    """
    try:
        import pytesseract
        from PIL import Image

        osd = pytesseract.image_to_osd(
            Image.open(image_path), config="--psm 0", output_type=pytesseract.Output.DICT
        )
        return int(osd.get("rotate", 0)) % 360
    except Exception:
        logger.debug("OSD orientation detection unavailable", exc_info=True)
        return 0


def rotate_quadrant(img: np.ndarray, degrees: int) -> np.ndarray:
    degrees %= 360
    if degrees == 0:
        return img
    if degrees == 90:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if degrees == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def apply_profile(img: np.ndarray, prof: dict) -> tuple[np.ndarray, dict]:
    """Run the profile's pipeline. Returns (image, metadata)."""
    meta: dict = {}

    img = cap_long_edge(img, prof.get("max_long_edge", 3000))

    if prof.get("grayscale"):
        img = to_gray(img)

    if prof.get("deskew", True):
        img, angle = deskew(img)
        meta["skew_deg"] = round(angle, 3)

    img = denoise(img, prof.get("denoise", "none"))

    if prof.get("binarize"):
        img = binarize(img, prof["binarize"])
        meta["binarized"] = prof["binarize"]

    meta["width_px"], meta["height_px"] = img.shape[1], img.shape[0]
    return img, meta
