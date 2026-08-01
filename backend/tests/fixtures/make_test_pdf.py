"""Generate the bilingual test document.

WeasyPrint, not ReportLab. WeasyPrint renders through Pango/HarfBuzz, which
performs Arabic shaping and the bidi algorithm correctly. ReportLab does
neither — it needs manual arabic_reshaper + python-bidi + font registration, and
any mistake yields a PDF whose Arabic LOOKS right but whose text layer is
garbage. That would silently break the very digital-PDF path these fixtures
exist to test.

Three variants from one source:
  digital.pdf       — real text layer            -> native_pdf, bidi repair
  scan_clean.pdf    — rasterized, image only     -> baseline OCR accuracy
  scan_degraded.pdf — rotated, noisy, JPEG'd     -> deskew, denoise, confidence

Content is deliberately adversarial: both scripts on one page, both digit
systems in one table, a Hijri AND a Gregorian date, a reference number, and a
low-contrast paragraph.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Ground truth values the integration test asserts on.
REF_NUMBER = "REF/2026/AR-0417"
AMOUNT_EN = "12,500"
AMOUNT_AR = "١٢٬٥٠٠"
TAX_EN = "1,875"
GREGORIAN = "2026-01-15"
HIJRI = "١٥ رجب ١٤٤٧ هـ"
ORG_AR = "شركة النور للتجارة المحدودة"
ORG_EN = "Al-Noor Trading Ltd"
PERSON_EN = "Ahmed Al-Rashid"
PERSON_AR = "أحمد الراشد"

HTML = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{ font-family: "Noto Naskh Arabic", "Noto Sans Arabic", "DejaVu Sans", serif;
          font-size: 13pt; line-height: 1.9; color: #000; }}
  h1 {{ font-size: 20pt; text-align: center; margin-bottom: 2mm; }}
  .sub {{ text-align: center; font-size: 11pt; margin-bottom: 8mm; }}
  .rtl {{ direction: rtl; text-align: right; }}
  .ltr {{ direction: ltr; text-align: left; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6mm 0; font-size: 12pt; }}
  th, td {{ border: 1px solid #222; padding: 2.5mm 3mm; }}
  .faint {{ color: #8a8a8a; }}   /* deliberately low contrast */
  .meta {{ font-size: 11pt; }}
</style></head>
<body>
  <h1 class="rtl">{ORG_AR}</h1>
  <div class="sub ltr">{ORG_EN}</div>

  <p class="rtl meta">رقم المرجع: {REF_NUMBER} &nbsp;&nbsp;&nbsp; التاريخ: {HIJRI}</p>
  <p class="ltr meta">Reference: {REF_NUMBER} &nbsp;&nbsp;&nbsp; Date: {GREGORIAN}</p>

  <p class="rtl">هذا عقد إيجار سنوي بين {ORG_AR} و{PERSON_AR}، بمبلغ إجمالي
  قدره {AMOUNT_AR} ريال سعودي سنوياً، ويسري اعتباراً من تاريخ التوقيع أدناه.</p>

  <p class="ltr">This annual lease agreement is made between {ORG_EN} and
  {PERSON_EN}, for the total sum of SAR {AMOUNT_EN} per year, effective
  {GREGORIAN}. The agreement renews automatically unless terminated in writing
  with thirty days notice.</p>

  <table>
    <tr><th class="rtl">البند</th><th class="rtl">المبلغ</th><th class="ltr">Amount</th></tr>
    <tr><td class="rtl">الإيجار السنوي</td><td class="rtl">{AMOUNT_AR}</td><td class="ltr">{AMOUNT_EN}</td></tr>
    <tr><td class="rtl">ضريبة القيمة المضافة</td><td class="rtl">١٬٨٧٥</td><td class="ltr">{TAX_EN}</td></tr>
  </table>

  <p class="rtl faint">ملاحظة: تخضع هذه الاتفاقية لأنظمة المملكة العربية السعودية،
  وأي نزاع ينشأ عنها يحال إلى الجهات المختصة في مدينة الرياض.</p>

  <p class="ltr">Signed: {PERSON_EN} &mdash; on behalf of {ORG_EN}.</p>
</body></html>
"""

GROUND_TRUTH = {
    "ref_number": REF_NUMBER,
    "amount_en": AMOUNT_EN,
    "amount_ar": AMOUNT_AR,
    "tax_en": TAX_EN,
    "gregorian_date": GREGORIAN,
    "hijri_date": HIJRI,
    "organization_ar": ORG_AR,
    "organization_en": ORG_EN,
    "person_en": PERSON_EN,
    "person_ar": PERSON_AR,
    "expected_doc_type": "contract",
    "arabic_only_phrase": "يحال إلى الجهات المختصة في مدينة الرياض",
    "english_only_phrase": "renews automatically unless terminated in writing",
    "skew_deg": 1.7,
}


def build_digital(out_dir: str) -> str:
    from weasyprint import HTML as WeasyHTML

    path = os.path.join(out_dir, "digital.pdf")
    WeasyHTML(string=HTML, base_url=HERE).write_pdf(path)
    return path


def _render_pages(pdf_path: str, dpi: int = 300):
    import numpy as np
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            image = page.render(scale=dpi / 72.0).to_pil().convert("RGB")
            page.close()
            yield np.array(image)
    finally:
        pdf.close()


def _images_to_pdf(images, path: str) -> str:
    from PIL import Image

    pils = [Image.fromarray(img) for img in images]
    pils[0].save(path, save_all=True, append_images=pils[1:], resolution=300.0)
    return path


def build_scan_clean(out_dir: str, digital_pdf: str) -> str:
    path = os.path.join(out_dir, "scan_clean.pdf")
    return _images_to_pdf(list(_render_pages(digital_pdf)), path)


def build_scan_degraded(out_dir: str, digital_pdf: str) -> str:
    """Rotate, add noise and JPEG artefacts — exercises the whole preprocessor."""
    import cv2
    import numpy as np

    path = os.path.join(out_dir, "scan_degraded.pdf")
    degraded = []

    for image in _render_pages(digital_pdf):
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]

        # Known skew the deskew test asserts on.
        matrix = cv2.getRotationMatrix2D((w // 2, h // 2), GROUND_TRUTH["skew_deg"], 1.0)
        bgr = cv2.warpAffine(
            bgr, matrix, (w, h), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
        )

        bgr = cv2.GaussianBlur(bgr, (3, 3), 0.6)
        rng = np.random.default_rng(42)  # deterministic, so CER is comparable
        noise = rng.normal(0, 8, bgr.shape)
        bgr = np.clip(bgr.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        ok, encoded = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 55])
        if ok:
            bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)

        degraded.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    return _images_to_pdf(degraded, path)


def build_all(out_dir: str | None = None) -> dict:
    out_dir = out_dir or HERE
    os.makedirs(out_dir, exist_ok=True)

    digital = build_digital(out_dir)
    result = {
        "digital": digital,
        "scan_clean": build_scan_clean(out_dir, digital),
        "scan_degraded": build_scan_degraded(out_dir, digital),
    }

    with open(os.path.join(out_dir, "ground_truth.json"), "w", encoding="utf-8") as fh:
        json.dump(GROUND_TRUTH, fh, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else HERE
    for name, path in build_all(target).items():
        print(f"{name:14} {path}  ({os.path.getsize(path) // 1024} KB)")
