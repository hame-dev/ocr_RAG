"""Stage 2: background, cached, in-place section context for chunks."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from common import fsm
from documents.models import Document, TextRevision
from rag import chunking
from rag.contextualize import build_windows, merge_keywords, summarize_window, window_key

# ---- pure helpers -------------------------------------------------------------


@dataclass
class _C:
    text: str
    section_path: str = ""
    token_count: int = 100


def test_windows_break_on_section_change_chunk_count_and_tokens():
    chunks = (
        [_C(f"a{i}", "Intro") for i in range(8)]
        + [_C("b0", "Terms", 2500), _C("b1", "Terms", 600), _C("b2", "Terms", 100)]
    )
    windows = build_windows(chunks, max_tokens=3000, max_chunks=6)
    assert [[c.text for c in w] for w in windows] == [
        ["a0", "a1", "a2", "a3", "a4", "a5"],
        ["a6", "a7"],
        ["b0"],  # b1 would push the window past 3000 tokens
        ["b1", "b2"],
    ]


def test_an_oversized_chunk_is_a_window_on_its_own():
    windows = build_windows([_C("big", "", 9000), _C("small", "")], max_tokens=3000, max_chunks=6)
    assert [[c.text for c in w] for w in windows] == [["big"], ["small"]]


def test_window_key_depends_on_title_section_text_model_and_version():
    base = window_key("Lease", "1. Rent", "text", "m", "v1")
    assert base == window_key("Lease", "1. Rent", "text", "m", "v1")
    assert len({
        base,
        window_key("Invoice", "1. Rent", "text", "m", "v1"),
        window_key("Lease", "2. Rent", "text", "m", "v1"),
        window_key("Lease", "1. Rent", "text!", "m", "v1"),
        window_key("Lease", "1. Rent", "text", "m2", "v1"),
        window_key("Lease", "1. Rent", "text", "m", "v2"),
    }) == 6


def test_merge_keywords_dedupes_case_insensitively_and_caps():
    assert merge_keywords(["Rent", "deposit"], ["rent", "notice", "fee"], cap=3) == ["Rent", "deposit", "notice"]


class _JSONClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate_json(self, model, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        return reply, str(reply)


def test_summarize_repairs_once_then_gives_up():
    client = _JSONClient([None, {"summary": "Rent terms.", "keywords": ["rent", "  ", "x" * 200]}])
    out = summarize_window(client, "m", "Lease", "A lease.", "1. Rent", "Rent is due.", "en")
    assert out == {"summary": "Rent terms.", "keywords": ["rent"]}
    assert len(client.prompts) == 2 and "invalid" in client.prompts[1].lower()

    client = _JSONClient([{"summary": 5}, {"keywords": []}])
    assert summarize_window(client, "m", "Lease", "", "", "Rent is due.", "en") is None


def test_summary_is_capped_and_asks_for_the_document_language():
    client = _JSONClient([{"summary": "ب" * 500, "keywords": []}])
    out = summarize_window(client, "m", "عقد", "", "", "نص", "ar")
    assert len(out["summary"]) == 300
    assert "Arabic" in client.prompts[0]


# ---- the task -----------------------------------------------------------------

SENTENCE = "The tenant shall pay the annual rent on the first day of each month. "
BODY = SENTENCE * 12


class _FakeOllama:
    def __init__(self, on_generate=None):
        self.generate_calls = 0
        self.embed_inputs: list[str] = []
        self.on_generate = on_generate

    def has_model(self, model):
        return True

    def embed(self, texts):
        self.embed_inputs.extend(texts)
        return [[0.2] * 1024 for _ in texts]

    def generate_json(self, model, prompt, schema, **kwargs):
        self.generate_calls += 1
        if self.on_generate:
            self.on_generate()
        section = prompt.split("SECTION: ", 1)[1].split("\n", 1)[0]
        reply = {"summary": f"About {section}.", "keywords": [f"kw {section}", "tenant obligations"]}
        return reply, str(reply)


@pytest.fixture
def indexed(user, tmp_path, settings, monkeypatch):
    from rag.tasks import index_document

    settings.MEDIA_ROOT = str(tmp_path)
    settings.CHUNK_CONTEXT_ENABLED = False
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)
    doc = Document.objects.create(
        owner=user, title="Lease", original_filename="lease.pdf", mime_type="application/pdf",
        storage_path=str(tmp_path / "lease.pdf"), status=fsm.READY,
    )
    pages = ["INTRODUCTION\n" + BODY, BODY, BODY, "2. Payment Terms\n" + BODY, BODY, BODY, BODY, BODY]
    revision = TextRevision.objects.create(document=doc, revision_no=1, source="manual_entry", text="\f".join(pages))
    doc.current_revision = revision
    doc.save()
    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeOllama())
    index_document(str(doc.id))
    settings.CHUNK_CONTEXT_ENABLED = True
    return doc


def _chunks(doc):
    from rag.models import Chunk

    return list(Chunk.objects.filter(document=doc).order_by("chunk_index"))


@pytest.mark.django_db
def test_contextualize_updates_chunks_in_place_and_caches(indexed, monkeypatch):
    from rag.models import ChunkContext, IndexRun
    from rag.tasks import CONTEXT_VERSION, queue_contextualize

    before = _chunks(indexed)
    assert len(before) == 8
    fake = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)

    assert queue_contextualize(str(indexed.id)) is True

    after = _chunks(indexed)
    assert [c.id for c in after] == [c.id for c in before]
    assert fake.generate_calls == 2  # one per window: INTRODUCTION (3), Payment Terms (5)
    assert ChunkContext.objects.count() == 2
    first, last = after[0], after[-1]
    assert first.meta["chunk"]["summary"] == "About INTRODUCTION."
    assert first.meta["chunk"]["context_version"] == CONTEXT_VERSION
    assert first.meta["chunk"]["context_model"]
    assert first.context_text == "INTRODUCTION. About INTRODUCTION."
    assert "kw INTRODUCTION" in first.keywords_text
    assert last.meta["chunk"]["summary"] == "About 2. Payment Terms."
    assert any("Section: 2. Payment Terms. About 2. Payment Terms." in text for text in fake.embed_inputs)

    run = IndexRun.objects.filter(document=indexed).first()
    assert (run.context_status, run.context_windows, run.context_done) == ("succeeded", 2, 2)

    # Re-running: every window is a cache hit and nothing changed, so no LLM
    # call and no re-embedding.
    again = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: again)
    queue_contextualize(str(indexed.id))
    assert again.generate_calls == 0
    assert again.embed_inputs == []


@pytest.mark.django_db
def test_a_reindex_is_free_once_cached(indexed, monkeypatch):
    from rag.tasks import index_document

    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeOllama())
    from rag.tasks import queue_contextualize

    queue_contextualize(str(indexed.id))

    fake = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    index_document(str(indexed.id))  # CHUNK_CONTEXT_ENABLED: stage 2 runs inline (eager)
    assert fake.generate_calls == 0
    assert all(c.meta["chunk"]["summary"] for c in _chunks(indexed))


@pytest.mark.django_db
def test_a_revision_change_mid_run_aborts_without_writes(indexed, monkeypatch):
    from rag.models import IndexRun
    from rag.tasks import queue_contextualize

    def new_revision():
        revision = TextRevision.objects.create(
            document=indexed, revision_no=2, source="manual_edit", text="changed",
            parent=indexed.current_revision,
        )
        Document.objects.filter(id=indexed.id).update(current_revision=revision)

    fake = _FakeOllama(on_generate=new_revision)
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    queue_contextualize(str(indexed.id))

    assert fake.generate_calls == 1
    assert all(c.meta["chunk"]["summary"] == "" for c in _chunks(indexed))
    run = IndexRun.objects.filter(document=indexed).first()
    assert run.context_status == "aborted"


@pytest.mark.django_db
def test_disabled_setting_queues_nothing(indexed, settings, monkeypatch):
    from rag.tasks import queue_contextualize

    settings.CHUNK_CONTEXT_ENABLED = False
    fake = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    assert queue_contextualize(str(indexed.id)) is False
    assert fake.generate_calls == 0


@pytest.mark.django_db
def test_reindex_all_contextualize_only(indexed, settings, monkeypatch):
    from django.core.management import call_command

    settings.CHUNK_CONTEXT_ENABLED = False  # an explicit command runs anyway
    fake = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    call_command("reindex_all", "--contextualize-only")
    assert fake.generate_calls == 2


def test_contextualize_routes_to_llm_bg_and_index_stays_on_index():
    from celery import current_app

    import config.celery  # noqa: F401  (configures the app)

    router = current_app.amqp.router
    assert router.route({}, "rag.tasks.contextualize_document")["queue"].name == "llm_bg"
    assert router.route({}, "rag.tasks.index_document")["queue"].name == "index"


def _locks(queries) -> list[str]:
    return [q["sql"] for q in queries if "FOR NO KEY UPDATE" in q["sql"] and "documents_document" in q["sql"]]


@pytest.mark.django_db
def test_index_and_stage2_serialize_on_the_same_document_row_lock(indexed, monkeypatch, settings):
    """Both writers take FOR NO KEY UPDATE on the document first, so they queue
    behind each other instead of deadlocking on chunk rows; NO KEY does not
    block the FK check of the chunks being inserted."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from rag.tasks import index_document, queue_contextualize

    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeOllama())
    with CaptureQueriesContext(connection) as stage2:
        queue_contextualize(str(indexed.id))
    assert _locks(stage2.captured_queries)

    settings.CHUNK_CONTEXT_ENABLED = False
    with CaptureQueriesContext(connection) as stage1:
        index_document(str(indexed.id))
    assert _locks(stage1.captured_queries)


@pytest.mark.django_db
def test_chunks_replaced_by_a_reindex_abort_the_window(indexed, monkeypatch):
    from rag.models import Chunk, IndexRun
    from rag.tasks import queue_contextualize

    def reindex_behind_our_back():
        # Same revision, new chunk rows: what a concurrent index_document does.
        for chunk in Chunk.objects.filter(document=indexed):
            Chunk.objects.filter(id=chunk.id).delete()

    fake = _FakeOllama(on_generate=reindex_behind_our_back)
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    queue_contextualize(str(indexed.id))
    run = IndexRun.objects.filter(document=indexed).first()
    assert (run.context_status, run.context_error) == ("aborted", "chunks were replaced by a re-index")


@pytest.mark.django_db
def test_stage2_yields_the_llm_worker_after_a_few_windows(indexed, monkeypatch, settings):
    """A long document must not hold the single LLM worker for its whole run:
    after CHUNK_CONTEXT_WINDOWS_PER_TASK LLM calls the task re-queues itself, so
    anything waiting on the higher-priority llm queue runs in between."""
    from rag import tasks
    from rag.models import IndexRun

    settings.CHUNK_CONTEXT_WINDOWS_PER_TASK = 1
    queued = []
    original = tasks.contextualize_document.apply_async

    def spy(*args, **kwargs):
        queued.append(kwargs.get("queue"))
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks.contextualize_document, "apply_async", spy)
    fake = _FakeOllama()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)

    tasks.queue_contextualize(str(indexed.id))

    assert queued == ["llm_bg", "llm_bg"]  # the first run, then one continuation
    assert fake.generate_calls == 2
    run = IndexRun.objects.filter(document=indexed).first()
    assert (run.context_status, run.context_done, run.context_windows) == ("succeeded", 2, 2)


# ---- review fixes ------------------------------------------------------------


@pytest.mark.django_db
def test_windows_that_fail_validation_do_not_loop_the_task_forever(indexed, monkeypatch, settings):
    """Every window fails validation: the task must still finish (each failed
    window is remembered so the next run skips it), not re-queue itself with
    the same work forever."""
    from rag import tasks
    from rag.models import IndexRun

    settings.CHUNK_CONTEXT_WINDOWS_PER_TASK = 1

    class Garbage(_FakeOllama):
        def generate_json(self, model, prompt, schema, **kwargs):
            self.generate_calls += 1
            return {"summary": "", "keywords": []}, '{"summary": ""}'

    fake = Garbage()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    runs = []
    original = tasks.contextualize_document.apply_async
    monkeypatch.setattr(tasks.contextualize_document, "apply_async",
                        lambda *a, **k: (runs.append(1), original(*a, **k))[1])

    tasks.queue_contextualize(str(indexed.id))

    run = IndexRun.objects.filter(document=indexed).first()
    assert run.context_status == "succeeded"
    assert len(runs) <= 3  # first run + at most one continuation per window
    # 2 windows x (call + one repair), never more.
    assert fake.generate_calls == 4
    assert all(c.meta["chunk"]["summary"] == "" for c in _chunks(indexed))

    # And a later run makes no LLM calls for those windows.
    again = Garbage()
    monkeypatch.setattr("rag.tasks.get_client", lambda: again)
    tasks.queue_contextualize(str(indexed.id))
    assert again.generate_calls == 0


@pytest.mark.django_db
def test_an_enqueue_failure_after_indexing_keeps_the_document_ready(indexed, monkeypatch, settings):
    from rag import tasks
    from rag.models import IndexRun

    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeOllama())

    def broker_down(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(tasks.contextualize_document, "apply_async", broker_down)
    tasks.index_document(str(indexed.id))

    indexed.refresh_from_db()
    assert indexed.status == fsm.READY
    run = IndexRun.objects.filter(document=indexed).first()
    assert run.status == "succeeded"
    assert run.context_status == "failed"


@pytest.mark.django_db
def test_a_transient_ollama_error_requeues_stage_2_instead_of_failing_it(indexed, monkeypatch):
    from common.ollama import OllamaError
    from rag import tasks
    from rag.models import IndexRun

    class Flaky(_FakeOllama):
        def generate_json(self, model, prompt, schema, **kwargs):
            if self.generate_calls == 0:
                self.generate_calls += 1
                raise OllamaError("connection refused")
            return super().generate_json(model, prompt, schema, **kwargs)

    fake = Flaky()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    tasks.queue_contextualize(str(indexed.id))

    run = IndexRun.objects.filter(document=indexed).first()
    assert run.context_status == "succeeded"
    assert all(c.meta["chunk"]["summary"] for c in _chunks(indexed))


@pytest.mark.django_db
def test_stage_2_gives_up_after_repeated_ollama_errors(indexed, monkeypatch):
    from common.ollama import OllamaError
    from rag import tasks
    from rag.models import IndexRun

    class Down(_FakeOllama):
        def generate_json(self, model, prompt, schema, **kwargs):
            self.generate_calls += 1
            raise OllamaError("connection refused")

    fake = Down()
    monkeypatch.setattr("rag.tasks.get_client", lambda: fake)
    tasks.queue_contextualize(str(indexed.id))

    run = IndexRun.objects.filter(document=indexed).first()
    assert run.context_status == "failed"
    assert "connection refused" in run.context_error
    assert fake.generate_calls == tasks.MAX_CONTEXT_RETRIES + 1


def test_context_model_only_falls_back_when_the_model_is_known_to_be_missing(settings):
    from common.ollama import OllamaUnavailable
    from rag.tasks import context_model

    settings.CHUNK_CONTEXT_MODEL, settings.LLM_MODEL = "small", "big"

    class Has:
        def has_model(self, model):
            return True

    class Missing:
        def has_model(self, model):
            return False

    class CannotTell:
        def has_model(self, model):
            raise OllamaUnavailable("tags timed out")

    assert context_model(Has()) == "small"
    assert context_model(Missing()) == "big"
    # A transient check failure must not silently switch the whole document
    # to the big model (and a different cache key); let the task retry.
    with pytest.raises(OllamaUnavailable):
        context_model(CannotTell())


@pytest.mark.django_db
def test_stage_2_does_not_load_chunk_embeddings_it_never_reads(indexed, monkeypatch):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from rag.tasks import queue_contextualize

    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeOllama())
    with CaptureQueriesContext(connection) as ctx:
        queue_contextualize(str(indexed.id))
    selects = [q["sql"] for q in ctx.captured_queries
               if q["sql"].startswith("SELECT") and '"rag_chunk"."text"' in q["sql"]]
    assert selects, "expected the chunk load query"
    assert all('"rag_chunk"."embedding"' not in sql for sql in selects)
