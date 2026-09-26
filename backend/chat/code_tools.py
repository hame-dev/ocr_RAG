"""General-mode tools: run Python in the sandbox and keep the files it makes.

Only General mode binds these (graph.GENERAL_TOOLS, run by their own ToolNode),
so Documents mode can never execute code. The code itself runs in the
code-runner sidecar (docker/code-runner), never in this process.

A tool result has two halves (response_format="content_and_artifact"):
  content   what the model reads back: output and file names, kept small;
  artifact  what the UI shows: the code, its output and the saved file ids.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
from datetime import timedelta
from typing import Annotated

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from rest_framework.decorators import api_view

from chat.models import GeneratedFile

logger = logging.getLogger(__name__)

# extension -> (mime, kind)
FILE_TYPES: dict[str, tuple[str, str]] = {
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".svg": ("image/svg+xml", "image"),
    ".pdf": ("application/pdf", "document"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "spreadsheet"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "presentation"),
    ".csv": ("text/csv", "data"),
    ".txt": ("text/plain", "data"),
    ".json": ("application/json", "data"),
    ".md": ("text/markdown", "data"),
}
# Shown in the page itself. Everything else, SVG included (it can carry
# script), is served as a download.
INLINE_MIMES = {"image/png", "image/jpeg"}
MODEL_OUTPUT_CHARS = 4000
_UNSAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)


class RunnerUnavailable(RuntimeError):
    pass


def generated_dir(file_id) -> str:
    return os.path.join(settings.MEDIA_ROOT, "chat", "generated", str(file_id))


def safe_filename(name: str) -> str | None:
    """A plain basename with an allowed extension, or None."""
    base = os.path.basename(name or "").strip()
    stem, extension = os.path.splitext(base)
    extension = extension.lower()
    if extension not in FILE_TYPES:
        return None
    stem = _UNSAFE_NAME.sub("_", stem).strip(" ._")[:120] or "file"
    return stem + extension


def serialize_file(file: GeneratedFile) -> dict:
    return {
        "id": str(file.id),
        "filename": file.filename,
        "kind": file.kind,
        "mime": file.mime,
        "size": file.size,
        "inline": file.mime in INLINE_MIMES,
        "url": f"/api/chat/files/{file.id}/",
    }


async def call_runner(code: str) -> dict:
    """POST the code to the sandbox. Raises RunnerUnavailable when it cannot run."""
    timeout = settings.CODE_RUN_TIMEOUT_S
    try:
        async with httpx.AsyncClient(timeout=timeout + 15) as client:
            response = await client.post(
                f"{settings.CODE_RUNNER_URL.rstrip('/')}/run",
                json={"code": code, "timeout_s": timeout},
            )
    except httpx.HTTPError as exc:
        logger.warning("code runner unreachable: %s", exc)
        raise RunnerUnavailable("the code runner is not reachable") from exc
    if response.status_code == 429:
        raise RunnerUnavailable("the code runner is busy; try again in a moment")
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            pass
        raise RunnerUnavailable(detail or f"the code runner failed ({response.status_code})")
    return response.json()


@sync_to_async(thread_sensitive=True)
def save_files(files: list[dict], user_id, conversation_id) -> list[GeneratedFile]:
    saved: list[GeneratedFile] = []
    for item in files:
        name = safe_filename(str(item.get("name") or ""))
        if name is None:
            continue
        try:
            data = base64.b64decode(item.get("b64") or "", validate=True)
        except (binascii.Error, ValueError):
            logger.warning("code runner returned undecodable file %r", item.get("name"))
            continue
        mime, kind = FILE_TYPES[os.path.splitext(name)[1]]
        record = GeneratedFile(
            owner_id=user_id, conversation_id=conversation_id,
            kind=kind, filename=name, mime=mime, size=len(data),
        )
        directory = generated_dir(record.id)
        os.makedirs(directory, exist_ok=True)
        record.storage_path = os.path.join(directory, name)
        with open(record.storage_path, "wb") as fh:
            fh.write(data)
        record.save()
        saved.append(record)
    return saved


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= MODEL_OUTPUT_CHARS else text[:MODEL_OUTPUT_CHARS] + "\n[… truncated]"


@tool(response_format="content_and_artifact")
async def run_python(
    code: str,
    user_id: Annotated[int | None, InjectedState("user_id")],
    conversation_id: Annotated[str | None, InjectedState("conversation_id")],
) -> tuple[str, dict]:
    """Run Python 3 code in a sandbox and return what it printed plus any files it saved.

    Use it for every calculation (arithmetic, percentages, statistics, algebra,
    calculus, equations, matrices), for charts, and to create Excel, Word or
    PowerPoint files. Available: math, statistics, numpy, scipy, sympy, pandas,
    matplotlib, openpyxl, python-docx (import docx), python-pptx (import pptx),
    arabic_reshaper and bidi. No internet, no input(), 30-second limit.

    print() every result you need to see. Save files to the current directory
    with a short descriptive name (e.g. plt.savefig("sales_by_region.png"),
    wb.save("scores.xlsx"), doc.save("report.docx"), prs.save("deck.pptx"));
    never call plt.show(). Saved files are shown to the user automatically.

    Args:
        code: A complete Python script.
    """
    artifact = {"code": code, "ok": False, "stdout": "", "stderr": "", "duration_ms": None, "file_ids": []}
    # Never raise: ToolNode would turn the exception into a message for the
    # model, but the stream would get no on_tool_end, so the UI could not show
    # the run. Every failure comes back as a failed run instead.
    try:
        result = await call_runner(code)
        files = await save_files(result.get("files") or [], user_id, conversation_id) if user_id else []
    except RunnerUnavailable as exc:
        artifact["stderr"] = f"Code execution is unavailable: {exc}."
        return json.dumps({"ok": False, "error": artifact["stderr"]}), artifact
    except Exception:
        logger.exception("run_python failed")
        artifact["stderr"] = "Code execution failed unexpectedly; the result could not be read."
        return json.dumps({"ok": False, "error": artifact["stderr"]}), artifact

    artifact.update(
        ok=bool(result.get("ok")),
        stdout=result.get("stdout") or "",
        stderr=result.get("stderr") or "",
        duration_ms=result.get("duration_ms"),
        file_ids=[str(f.id) for f in files],
    )
    content = {
        "ok": artifact["ok"],
        "stdout": _clip(artifact["stdout"]),
        "stderr": _clip(artifact["stderr"]),
        "files_saved": [f.filename for f in files],
    }
    return json.dumps(content, ensure_ascii=False), artifact


GENERAL_TOOLS = [run_python]


def delete_stale_unbound_files(max_age: timedelta = timedelta(hours=24)) -> int:
    """Files whose turn never finished (the tab closed mid-run) and so never got a message."""
    stale = GeneratedFile.objects.filter(message__isnull=True, created_at__lt=timezone.now() - max_age)
    count = 0
    for record in stale:
        record.delete()  # the post_delete signal removes the file on disk
        count += 1
    return count


# ---- Views -------------------------------------------------------------------

@api_view(["GET"])
def download_generated_file(request, file_id):
    record = GeneratedFile.objects.filter(id=file_id, owner=request.user).first()
    if record is None or not os.path.exists(record.storage_path):
        raise Http404("no such file")
    response = FileResponse(
        open(record.storage_path, "rb"),
        content_type=record.mime,
        as_attachment=record.mime not in INLINE_MIMES,
        filename=record.filename,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
