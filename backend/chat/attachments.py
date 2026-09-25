"""Chat attachments: images, PDFs and text/Office files for General mode.

Files are validated by content (never by extension or the client's MIME type)
and normalized on upload, so what reaches the model is bounded and predictable:
  image       → re-encoded JPEG, EXIF stripped, long edge ≤ 1600 px
  PDF, text   → its text layer (the same trust checks as document ingestion)
  PDF, scan   → its first pages rendered to JPEG for the vision model
  .docx       → paragraphs and table cells
  .txt/.md/.csv → decoded text
"""
from __future__ import annotations

import base64
import io
import logging
import os
import zipfile
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from chat.models import ChatAttachment

logger = logging.getLogger(__name__)

MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGE_EDGE = 1600
SCANNED_PDF_PAGES = 4
PDF_RENDER_DPI = 150
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log"}
# Per turn: how much attachment text goes into the prompt, and how many images.
TEXT_BUDGET_CHARS = 16_000
MAX_IMAGES_PER_CONVERSATION = 8
MAX_ATTACHMENTS_PER_MESSAGE = 5


class UnsupportedFile(ValueError):
    pass


def _attachment_dir(attachment_id) -> str:
    path = os.path.join(settings.MEDIA_ROOT, "chat", str(attachment_id))
    os.makedirs(path, exist_ok=True)
    return path


def _save_jpeg(image, path: str) -> None:
    from PIL import Image

    image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
    image.save(path, "JPEG", quality=85, optimize=True)


def _process_image(data: bytes, directory: str) -> dict:
    from PIL import Image, ImageOps

    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()  # structural check; the image must be reopened afterwards
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as exc:
        raise UnsupportedFile("not a readable image") from exc
    path = os.path.join(directory, "image.jpg")
    _save_jpeg(image, path)  # re-encoding drops EXIF and any trailing payload
    return {"kind": "image", "mime": "image/jpeg", "page_images": [path], "page_count": 1}


def _process_pdf(path: str, directory: str) -> dict:
    import pypdfium2 as pdfium
    from PIL import Image

    from documents.services.pdf_text import analyze_pdf

    report = analyze_pdf(path)
    if report.get("reason", "").startswith("not_a_pdf"):
        raise UnsupportedFile("not a readable PDF")

    result = {"kind": "pdf", "mime": "application/pdf", "page_count": report.get("page_count")}
    if report.get("is_digital"):
        result["extracted_text"] = "\f".join(p["text"] for p in report["pages"]).strip()
        return result

    # A scan (or an untrustworthy text layer): let the vision model read pages.
    pdf = pdfium.PdfDocument(path)
    try:
        images = []
        for index in range(min(len(pdf), SCANNED_PDF_PAGES)):
            page = pdf[index]
            try:
                pil: Image.Image = page.render(scale=PDF_RENDER_DPI / 72.0).to_pil()
            finally:
                page.close()
            out = os.path.join(directory, f"page-{index + 1}.jpg")
            _save_jpeg(pil, out)
            images.append(out)
    finally:
        pdf.close()
    result["page_images"] = images
    return result


def _process_docx(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise UnsupportedFile("not a Word document")
        import docx

        document = docx.Document(io.BytesIO(data))
    except UnsupportedFile:
        raise
    except Exception as exc:
        raise UnsupportedFile("not a readable Word document") from exc

    lines = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return {
        "kind": "text",
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "extracted_text": "\n".join(lines),
    }


def _process_text(data: bytes) -> dict:
    if b"\x00" in data[:4096]:
        raise UnsupportedFile("binary file")
    for encoding in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return {"kind": "text", "mime": "text/plain", "extracted_text": text}


def process_upload(upload, owner) -> ChatAttachment:
    """Validate, normalize and store one uploaded file."""
    if upload.size > MAX_BYTES:
        raise UnsupportedFile(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")

    data = upload.read()
    name = os.path.basename(upload.name or "file")[:255]
    extension = os.path.splitext(name)[1].lower()
    attachment = ChatAttachment(owner=owner, filename=name, size=len(data))
    directory = _attachment_dir(attachment.id)

    try:
        if data[:5] == b"%PDF-":
            original = os.path.join(directory, "original.pdf")
            with open(original, "wb") as fh:
                fh.write(data)
            fields = _process_pdf(original, directory)
            attachment.storage_path = original
        elif data[:4] == b"PK\x03\x04" and extension == ".docx":
            fields = _process_docx(data)
        elif extension in TEXT_EXTENSIONS:
            fields = _process_text(data)
        else:
            # Anything else must genuinely be an image.
            fields = _process_image(data, directory)
    except UnsupportedFile:
        _remove_dir(directory)
        raise
    except Exception as exc:
        _remove_dir(directory)
        logger.exception("attachment processing failed for %s", name)
        raise UnsupportedFile("could not read this file") from exc

    for key, value in fields.items():
        setattr(attachment, key, value)
    if not attachment.storage_path:
        attachment.storage_path = (attachment.page_images or [""])[0] or directory
    if attachment.kind != "image" and not attachment.extracted_text and not attachment.page_images:
        _remove_dir(directory)
        raise UnsupportedFile("no readable content found in this file")
    attachment.save()
    return attachment


def _remove_dir(directory: str) -> None:
    import shutil

    shutil.rmtree(directory, ignore_errors=True)


def serialize(attachment: ChatAttachment) -> dict:
    return {
        "id": str(attachment.id),
        "filename": attachment.filename,
        "kind": attachment.kind,
        "size": attachment.size,
        "pages": attachment.page_count,
        "has_preview": bool(attachment.page_images),
        "preview_url": f"/api/chat/attachments/{attachment.id}/preview/" if attachment.page_images else None,
    }


# ---- Building the model input ----------------------------------------------

def _data_uri(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        logger.warning("attachment image missing: %s", path)
        return None


def build_human_content(text: str, attachments: list[ChatAttachment], images_already_in_history: int):
    """The user turn as multimodal content: text, attachment text, then images.

    Returns a plain string when there is nothing to attach, so text-only turns
    are stored exactly as before.
    """
    if not attachments:
        return text

    parts: list[dict] = []
    budget = TEXT_BUDGET_CHARS
    blocks = []
    for attachment in attachments:
        header = f'<attachment name="{attachment.filename}" kind="{attachment.kind}"'
        if attachment.page_count:
            header += f' pages="{attachment.page_count}"'
        if attachment.extracted_text:
            body = attachment.extracted_text
            if len(body) > budget:
                body = body[: max(budget, 0)] + "\n[… truncated: the file is longer than the reading limit]"
            budget -= len(body)
            blocks.append(f"{header}>\n{body}\n</attachment>")
        elif attachment.page_images:
            blocks.append(f"{header}>\n[shown as {len(attachment.page_images)} image(s) below]\n</attachment>")

    parts.append({"type": "text", "text": "\n\n".join([text, *blocks]) if blocks else text})

    image_slots = max(MAX_IMAGES_PER_CONVERSATION - images_already_in_history, 0)
    for attachment in attachments:
        for path in attachment.page_images:
            if image_slots <= 0:
                parts.append({"type": "text", "text": f"[image from {attachment.filename} omitted: image limit reached]"})
                break
            uri = _data_uri(path)
            if uri:
                parts.append({"type": "image_url", "image_url": uri})
                image_slots -= 1
    return parts


def count_history_images(messages) -> int:
    total = 0
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, list):
            total += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image_url")
    return total


# ---- Housekeeping ---------------------------------------------------------------

def delete_stale_unbound(max_age: timedelta = timedelta(hours=24)) -> int:
    stale = ChatAttachment.objects.filter(conversation__isnull=True, created_at__lt=timezone.now() - max_age)
    count = 0
    for attachment in stale:
        _remove_dir(os.path.join(settings.MEDIA_ROOT, "chat", str(attachment.id)))
        attachment.delete()
        count += 1
    return count


# ---- Views -------------------------------------------------------------------

@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload_attachment(request):
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        attachment = process_upload(upload, request.user)
    except UnsupportedFile as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(serialize(attachment), status=status.HTTP_201_CREATED)


def _owned(request, attachment_id) -> ChatAttachment:
    attachment = ChatAttachment.objects.filter(id=attachment_id, owner=request.user).first()
    if attachment is None:
        raise Http404("no such attachment")
    return attachment


@api_view(["DELETE"])
def delete_attachment(request, attachment_id):
    attachment = _owned(request, attachment_id)
    if attachment.message_id is not None:
        # Sent attachments are part of the transcript; they go with the conversation.
        return Response({"detail": "attachment already sent"}, status=status.HTTP_409_CONFLICT)
    _remove_dir(os.path.join(settings.MEDIA_ROOT, "chat", str(attachment.id)))
    attachment.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def attachment_preview(request, attachment_id):
    attachment = _owned(request, attachment_id)
    if not attachment.page_images or not os.path.exists(attachment.page_images[0]):
        raise Http404("no preview")
    return FileResponse(open(attachment.page_images[0], "rb"), content_type="image/jpeg")
