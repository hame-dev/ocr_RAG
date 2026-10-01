"""Dev ingestion from a folder: the UI pipeline, driven by a management command."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from common import fsm
from documents.models import Document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    queued: list = []
    monkeypatch.setattr("ocr.tasks.preprocess_document.delay", lambda *a, **k: queued.append(a))
    return queued


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "scans"
    (root / "sub").mkdir(parents=True)
    pdf = (FIXTURES / "digital.pdf").read_bytes()
    (root / "a.pdf").write_bytes(pdf)
    (root / "b-copy.pdf").write_bytes(pdf)  # same content, different name
    (root / "notes.txt").write_text("not a document")
    (root / "sub" / "c.png").write_bytes(_png())
    return root


# ---- shared create helper -----------------------------------------------------


@pytest.mark.django_db
def test_create_document_stores_the_file_its_hash_and_owner(user, env, django_capture_on_commit_callbacks):
    from documents.serializers import UploadSerializer
    from documents.services.ingest import create_document

    data = (FIXTURES / "digital.pdf").read_bytes()
    serializer = UploadSerializer(data={"file": SimpleUploadedFile("digital.pdf", data)})
    assert serializer.is_valid(), serializer.errors

    with django_capture_on_commit_callbacks(execute=True):
        doc = create_document(user, serializer.validated_data["file"], title="Digital")

    assert doc.owner == user and doc.title == "Digital"
    assert doc.mime_type == "application/pdf" and doc.size_bytes == len(data)
    assert doc.sha256 == hashlib.sha256(data).hexdigest()
    assert Path(doc.storage_path).read_bytes() == data
    assert env == [(str(doc.id), ["neural"])]


@pytest.mark.django_db
def test_the_upload_endpoint_still_creates_documents_the_same_way(auth_client, user, env):
    data = (FIXTURES / "digital.pdf").read_bytes()
    response = auth_client.post("/api/documents/", {"file": SimpleUploadedFile("x.pdf", data), "title": "Via API"})
    assert response.status_code == 201, response.content
    doc = Document.objects.get(id=response.json()["id"])
    assert doc.owner == user and doc.sha256 == hashlib.sha256(data).hexdigest()


# ---- the command: which files, which skips ---------------------------------------


@pytest.mark.django_db
def test_ingest_folder_without_waiting_uploads_once_and_skips_the_rest(
    user, env, folder, django_capture_on_commit_callbacks,
):
    out = io.StringIO()
    # The command runs in autocommit outside tests; here the test transaction
    # would hold back the on_commit preprocess call.
    with django_capture_on_commit_callbacks(execute=True):
        call_command("ingest_folder", str(folder), "--user", "alice", "--no-wait", stdout=out)
    text = out.getvalue()

    docs = list(Document.objects.filter(owner=user))
    assert [d.original_filename for d in docs] == ["a.pdf"]
    assert "b-copy.pdf" in text and "already ingested" in text
    assert "notes.txt" in text and "only PDF and image files" in text
    assert "c.png" not in text  # subfolders need --recursive
    assert len(env) == 1

    # Re-running the same folder ingests nothing new.
    call_command("ingest_folder", str(folder), "--user", "alice", "--no-wait", stdout=io.StringIO())
    assert Document.objects.filter(owner=user).count() == 1


@pytest.mark.django_db
def test_ingest_folder_recursive_includes_subfolders(user, env, folder):
    call_command("ingest_folder", str(folder), "--user", "alice", "--no-wait", "--recursive", stdout=io.StringIO())
    assert sorted(Document.objects.filter(owner=user).values_list("original_filename", flat=True)) == ["a.pdf", "c.png"]


@pytest.mark.django_db
def test_ingest_folder_rejects_an_unknown_user_or_folder(user, tmp_path):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="no such user"):
        call_command("ingest_folder", str(tmp_path), "--user", "nobody", "--no-wait")
    with pytest.raises(CommandError, match="not a directory"):
        call_command("ingest_folder", str(tmp_path / "missing"), "--user", "alice", "--no-wait")


# ---- pipeline stages -------------------------------------------------------------


@pytest.fixture
def document(user, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return Document.objects.create(
        owner=user, title="Scan", original_filename="scan.pdf", mime_type="application/pdf",
        storage_path=str(tmp_path / "scan.pdf"), status=fsm.OCR_DONE,
    )


def _batch(document, *runs):
    from ocr.models import OCRBatch, OCRRun

    batch = OCRBatch.objects.create(document=document, requested_engines=[r[0] for r in runs], status="done")
    for engine, text, gibberish, status in runs:
        OCRRun.objects.create(
            batch=batch, document=document, engine_name=engine, status=status, text=text,
            gibberish_score=gibberish, mean_confidence=None,
        )
    return batch


@pytest.mark.django_db
def test_the_best_succeeded_run_becomes_the_finalized_revision(document):
    from documents.services.ingest import select_best_and_finalize

    batch = _batch(
        document,
        ("tesseract", "garbled text", 0.8, "succeeded"),
        ("chandra_ollama", "clean text", 0.1, "succeeded"),
        ("surya", "would be best", 0.0, "failed"),
    )
    revision = select_best_and_finalize(document, batch)

    document.refresh_from_db()
    assert revision.text == "clean text" and revision.is_final and revision.revision_no == 1
    assert document.current_revision_id == revision.id
    assert document.status == fsm.TEXT_FINALIZED


@pytest.mark.django_db
def test_a_batch_with_no_succeeded_run_is_an_error(document):
    from documents.services.ingest import IngestError, select_best_and_finalize

    batch = _batch(document, ("tesseract", "", 0.5, "failed"))
    with pytest.raises(IngestError, match="no OCR run succeeded"):
        select_best_and_finalize(document, batch)


@pytest.mark.django_db
def test_wait_for_status_returns_on_target_and_times_out_otherwise(document):
    from documents.services.ingest import IngestError, wait_for_status

    document.status = fsm.READY
    document.save()
    assert wait_for_status(document, {fsm.READY}, timeout=1, poll=0.01) == fsm.READY

    document.status = fsm.FAILED
    document.save()
    with pytest.raises(IngestError, match="failed"):
        wait_for_status(document, {fsm.READY}, timeout=1, poll=0.01)

    document.status = fsm.PREPROCESSING
    document.save()
    with pytest.raises(IngestError, match="timed out"):
        wait_for_status(document, {fsm.READY}, timeout=0.03, poll=0.01)


@pytest.mark.django_db
def test_process_document_drives_every_stage_in_order(document, monkeypatch):
    from documents.services import ingest

    document.status = fsm.UPLOADED
    document.save()
    stages: list[str] = []

    def fake_wait(doc, targets, *, timeout, poll):
        stages.append(f"wait:{sorted(targets)[0]}")
        return sorted(targets)[0]

    def fake_start_ocr_batch(doc, engines, languages, options):
        stages.append(f"ocr:{','.join(engines)}:{','.join(languages)}")
        doc.status = fsm.OCR_DONE
        doc.save()
        return _batch(doc, (engines[0], "ocr text", 0.1, "succeeded"))

    def fake_enrich(doc_id, auto_index):
        stages.append(f"enrich:auto_index={auto_index}")

    monkeypatch.setattr(ingest, "wait_for_status", fake_wait)
    monkeypatch.setattr("ocr.tasks.start_ocr_batch", fake_start_ocr_batch)
    monkeypatch.setattr("enrichment.tasks.enrich_document.delay", fake_enrich)

    ingest.process_document(document, engines=["chandra_ollama"], languages=["ara", "eng"], timeout=5, poll=0.01)

    assert stages == [
        "wait:preprocessed",
        "ocr:chandra_ollama:ara,eng",
        "wait:ocr_done",
        "enrich:auto_index=True",
        "wait:ready",
    ]
    document.refresh_from_db()
    assert document.current_revision.text == "ocr text"


@pytest.mark.django_db
def test_a_partial_ocr_batch_is_reported_and_left_for_review(document, monkeypatch):
    from documents.services import ingest

    def fake_start_ocr_batch(doc, engines, languages, options):
        doc.status = fsm.OCR_PARTIAL
        doc.save()
        return _batch(doc, ("chandra_ollama", "ocr text", 0.1, "succeeded"), ("tesseract", "", 0.5, "failed"))

    monkeypatch.setattr(ingest, "wait_for_status", lambda doc, targets, *, timeout, poll: doc.status)
    monkeypatch.setattr("ocr.tasks.start_ocr_batch", fake_start_ocr_batch)
    monkeypatch.setattr("enrichment.tasks.enrich_document.delay", lambda *a, **k: pytest.fail("enriched a partial batch"))

    with pytest.raises(ingest.IngestError, match="partially.*tesseract"):
        ingest.process_document(document, engines=["chandra_ollama", "tesseract"], languages=["ara"], timeout=5, poll=0.01)
    document.refresh_from_db()
    assert document.status == fsm.OCR_PARTIAL and document.current_revision is None
