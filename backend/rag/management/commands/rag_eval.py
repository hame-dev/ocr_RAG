"""Measure retrieval quality against a hand-written query set.

    python manage.py rag_eval --file queries.jsonl --user alice
    python manage.py rag_eval --file queries.jsonl --user alice --compare

Each line of the file is {"query": "...", "expected_document_id": "...",
"expected_pages": [3, 4]}. Write the set BEFORE changing retrieval and keep the
baseline JSON (`--json-out`) so every later change can be compared against it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from common.ownership import owned_documents
from rag.eval import evaluate, parse_cases


class Command(BaseCommand):
    help = "Run the retrieval evaluation set and print recall@k / MRR."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="JSONL of evaluation cases.")
        parser.add_argument("--user", required=True, help="Username whose documents are searched.")
        parser.add_argument("--top-k", type=int, default=10)
        parser.add_argument("--candidates", type=int, default=None, help="Fused rows fetched before reranking.")
        parser.add_argument("--rerank", action="store_true", help="Force the reranker on for this run.")
        parser.add_argument("--no-rerank", action="store_true", help="Disable the reranker for this run.")
        parser.add_argument("--compare", action="store_true", help="Run with and without the reranker.")
        parser.add_argument("--json-out", default=None, help="Write the report(s) to this path.")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"no such file: {path}")
        cases = parse_cases(path.read_text(encoding="utf-8"))
        if not cases:
            raise CommandError("the evaluation file has no cases")

        user = get_user_model().objects.filter(username=options["user"]).first()
        if user is None:
            raise CommandError(f"no such user: {options['user']}")
        doc_ids = [str(d) for d in owned_documents(user).values_list("id", flat=True)]

        from rag.search import hybrid_search

        def run(rerank: bool | None) -> dict:
            latencies: list[float] = []

            def search(query: str) -> list[dict]:
                started = time.monotonic()
                kwargs = {"top_k": options["top_k"], "doc_ids": doc_ids}
                if rerank is not None:
                    kwargs["rerank"] = rerank
                if options["candidates"] is not None:
                    kwargs["candidates"] = options["candidates"]
                hits = hybrid_search(query, **kwargs)
                latencies.append(time.monotonic() - started)
                return hits

            report = evaluate(cases, search, top_k=options["top_k"])
            report["mean_latency_s"] = round(sum(latencies) / len(latencies), 3) if latencies else None
            report["rerank"] = rerank
            return report

        if options["compare"]:
            reports = {"rerank": run(True), "no_rerank": run(False)}
        else:
            forced = True if options["rerank"] else False if options["no_rerank"] else None
            reports = {"run": run(forced)}

        for name, report in reports.items():
            self.stdout.write(self.style.MIGRATE_HEADING(name))
            for key, value in report.items():
                self.stdout.write(f"  {key:>16}: {value}")

        if options["json_out"]:
            Path(options["json_out"]).write_text(json.dumps(reports, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"wrote {options['json_out']}"))
