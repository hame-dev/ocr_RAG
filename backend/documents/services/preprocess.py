"""Document preprocessing: PDF/image -> analyzed pages + cached rasters."""
from __future__ import annotations

import logging
import os

import cv2
import numpy as np
import pypdfium2 as pdfium
from django.conf import settings

from common.arabic import detect_lang
from documents.models import Document, DocumentPage
from documents.services import pdf_text
from ocr.preprocess import images as imgproc
from ocr.preprocess.profiles import profile as get_profile
from ocr.preprocess.profiles import raster_key

logger = logging.getLogger(__name__)

IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/tiff", "image/webp", "image/bmp"}


def doc_dir(document: Document) -> str:
    return os.path.join(settings.MEDIA_ROOT, "docs", str(document.id))


def raster_dir(document: Document, profile_name: str, dpi: int) -> str:
    return os.path.join(doc_dir(document), "raster", raster_key(profile_name, dpi))


def analyze(document: Document) -> dict:
    """Detect page count, digital text layer, and languages. Fills DocumentPage rows."""
    path = document.storage_path

    if document.mime_type == "application/pdf":
        report = pdf_text.analyze_pdf(path)
        document.is_digital_pdf = report["is_digital"]
        document.digital_text_report = {
            k: v for k, v in report.items() if k != "pages"
        } | {
            # Keep per-page stats but drop the full text to keep the row small.
            "pages": [{k: v for k, v in p.items() if k != "text"} for p in report["pages"]]
        }
        document.page_count = report["page_count"]

        for page_info in report["pages"]:
            DocumentPage.objects.update_or_create(
                document=document,
                page_number=page_info["page_number"],
                defaults={
                    "native_text": page_info["text"] if report["is_digital"] else "",
                    "native_char_count": page_info["char_count"],
                },
            )
        combined = " ".join(p["text"] for p in report["pages"])
    else:
        document.is_digital_pdf = False
        document.page_count = 1
        document.digital_text_report = {"reason": "raster image; OCR required"}
        DocumentPage.objects.update_or_create(
            document=document, page_number=1, defaults={"native_text": ""}
        )
        combined = ""

    langs = []
    if combined.strip():
        lang = detect_lang(combined)
        langs = ["ar", "en"] if lang == "mixed" else [lang]
    document.detected_languages = langs

    document.save(
        update_fields=[
            "is_digital_pdf",
            "digital_text_report",
            "page_count",
            "detected_languages",
            "updated_at",
        ]
    )
    return document.digital_text_report


def _render_pdf_page(pdf, index: int, dpi: int) -> np.ndarray:
    page = pdf[index]
    try:
        bitmap = page.render(scale=dpi / 72.0)
        pil = bitmap.to_pil().convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    finally:
        page.close()


def rasterize(document: Document, profile_name: str, force: bool = False) -> list[str]:
    """Render + preprocess every page for one profile. Returns image paths.

    Rasters are cached on disk and reused across engines and across re-runs, so
    a 6-engine batch renders at most 3 times (one per distinct profile), not 6.
    """
    prof = get_profile(profile_name)
    dpi = prof["dpi"]
    out_dir = raster_dir(document, profile_name, dpi)
    os.makedirs(out_dir, exist_ok=True)

    is_pdf = document.mime_type == "application/pdf"
    pdf = pdfium.PdfDocument(document.storage_path) if is_pdf else None

    try:
        page_count = len(pdf) if pdf else 1
        paths: list[str] = []

        for i in range(page_count):
            page_number = i + 1
            out_path = os.path.join(out_dir, f"p{page_number}.png")

            if os.path.exists(out_path) and not force:
                paths.append(out_path)
                continue

            if pdf:
                img = _render_pdf_page(pdf, i, dpi)
            else:
                img = cv2.imread(document.storage_path, cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError(f"could not read image {document.storage_path}")

            # Orientation is detected once (on the first profile rendered) and
            # then reused, because OSD is not free.
            page_row = DocumentPage.objects.filter(
                document=document, page_number=page_number
            ).first()

            rotation = page_row.rotation_deg if page_row else 0
            if page_row and not page_row.preprocess_meta.get("osd_checked"):
                tmp = os.path.join(out_dir, f".osd_p{page_number}.png")
                cv2.imwrite(tmp, img)
                rotation = imgproc.detect_orientation(tmp)
                os.unlink(tmp)

            if rotation:
                img = imgproc.rotate_quadrant(img, int(rotation))

            img, meta = imgproc.apply_profile(img, prof)
            cv2.imwrite(out_path, img)
            paths.append(out_path)

            if page_row:
                page_row.rotation_deg = float(rotation or 0)
                page_row.skew_deg = meta.get("skew_deg", 0.0)
                page_row.width_px = meta.get("width_px", 0)
                page_row.height_px = meta.get("height_px", 0)
                page_row.raster_paths = {
                    **(page_row.raster_paths or {}),
                    raster_key(profile_name, dpi): out_path,
                }
                page_row.preprocess_meta = {
                    **(page_row.preprocess_meta or {}),
                    "osd_checked": True,
                    raster_key(profile_name, dpi): meta,
                }
                page_row.save()

            # A thumbnail for the library grid, generated once.
            if page_number == 1 and page_row and not page_row.thumbnail_path:
                thumb = os.path.join(doc_dir(document), "thumb.png")
                h, w = img.shape[:2]
                scale = 256.0 / max(h, w)
                cv2.imwrite(thumb, cv2.resize(img, None, fx=scale, fy=scale,
                                              interpolation=cv2.INTER_AREA))
                page_row.thumbnail_path = thumb
                page_row.save(update_fields=["thumbnail_path"])

        return paths
    finally:
        if pdf:
            pdf.close()
