"""Deep research over the user's documents.

A port of the docvision-rag DeepResearcher (understand → plan sub-queries →
search → check answers → retry gaps with alternative queries → synthesize →
follow-ups), with ChromaDB replaced by this project's hybrid search.

Retrieval is always scoped to state["doc_ids"], which chat_stream fills with the
caller's own documents, so per-user isolation holds exactly as it does for the
tool-calling agent. Passages carry their chunk_id and the writer cites them as
[[cite:<id>]], so the existing resolve_citations pipeline applies unchanged.
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chat.pipeline import emit, json_call, last_user_request, text_of
from chat.prompts import (
    LANGUAGE_RULE, MERMAID_RULE, RESEARCH_ALTERNATIVES, RESEARCH_CHECK, RESEARCH_FOLLOW_UPS,
    RESEARCH_PLAN, RESEARCH_WRITE, date_context,
)
from rag.search import format_hits, hybrid_search

logger = logging.getLogger(__name__)

MAX_SUB_QUERIES = 5
MAX_SEARCH_ATTEMPTS = 3
HITS_PER_QUERY = 6
MIN_CONFIDENCE = 0.6
EXCERPT_CHARS = 700
MAX_PASSAGES = 14


async def _search(query: str, doc_ids: list[str]) -> list[dict]:
    if not doc_ids:
        return []
    search = sync_to_async(hybrid_search, thread_sensitive=True)
    return format_hits(await search(query, top_k=HITS_PER_QUERY, doc_ids=doc_ids), max_chars=EXCERPT_CHARS)


async def deep_research(state) -> dict:
    from chat import graph  # lazy: graph imports this module

    doc_ids = list(state.get("doc_ids") or [])
    request = last_user_request(list(state["messages"]))
    question = text_of(request) if request else ""
    context = graph.history_context(state)
    retrieved: dict[str, dict] = dict(state.get("retrieved") or {})

    # 1-2. Understand and plan.
    await emit("phase", {"phase": "understanding"})
    plan = await json_call(
        graph._llm(with_tools=False, json=True),
        [
            SystemMessage(content=RESEARCH_PLAN.format(date_context=date_context(), max_steps=MAX_SUB_QUERIES)),
            *context,
            HumanMessage(content=question),
        ],
    )
    steps = [
        {"question": str(s.get("question")).strip(), "query": str(s.get("query") or s.get("question")).strip(),
         "confidence": 0.0, "answer": ""}
        for s in plan.get("steps") or []
        if isinstance(s, dict) and str(s.get("question") or "").strip()
    ][:MAX_SUB_QUERIES] or [{"question": question, "query": question, "confidence": 0.0, "answer": ""}]
    await emit("phase", {
        "phase": "planned",
        "detail": str(plan.get("understanding") or ""),
        "total": len(steps),
        "steps": [s["question"] for s in steps],
    })

    queries_run = 0
    for attempt in range(MAX_SEARCH_ATTEMPTS):
        pending = [s for s in steps if s["confidence"] < MIN_CONFIDENCE]
        if not pending:
            break

        # 3. Search (alternative phrasings after the first attempt).
        if attempt > 0:
            alternatives = await json_call(
                graph._llm(with_tools=False, json=True),
                [
                    SystemMessage(content=RESEARCH_ALTERNATIVES.format(date_context=date_context(), attempt=attempt)),
                    HumanMessage(content="\n".join(
                        f'- Question: "{s["question"]}" (previous search: "{s["query"]}")' for s in pending
                    )),
                ],
            )
            for step, query in zip(pending, alternatives.get("queries") or []):
                if isinstance(query, str) and query.strip():
                    step["query"] = query.strip()

        for step in pending:
            queries_run += 1
            await emit("phase", {
                "phase": "searching", "detail": step["query"], "index": queries_run,
                "attempt": attempt + 1,
            })
            hits = await _search(step["query"], doc_ids)
            for hit in hits:
                retrieved.setdefault(str(hit["chunk_id"]), hit)
            if hits:
                await emit("sources", {"hits": hits})

        if not retrieved:
            break

        # 4. Which sub-questions do the passages answer?
        await emit("phase", {"phase": "analyzing"})
        check = await json_call(
            graph._llm(with_tools=False, json=True),
            [
                SystemMessage(content=RESEARCH_CHECK),
                HumanMessage(content="Questions:\n" + "\n".join(s["question"] for s in steps)
                             + "\n\nPassages:\n" + _passages(retrieved)),
            ],
        )
        for result in check.get("results") or []:
            if not isinstance(result, dict):
                continue
            step = next((s for s in steps if s["question"] == result.get("question")), None)
            try:
                confidence = float(result.get("confidence") or 0)
            except (TypeError, ValueError):
                continue
            if step and confidence > step["confidence"]:
                step["confidence"] = confidence
                step["answer"] = str(result.get("answer") or "")
        answered = sum(1 for s in steps if s["confidence"] >= MIN_CONFIDENCE)
        await emit("phase", {"phase": "analyzed", "answered": answered, "total": len(steps)})

    # 5. Write (streamed; the only call tagged as the final answer).
    await emit("phase", {"phase": "writing", "sources": len(retrieved), "queries": queries_run})
    if retrieved:
        system = RESEARCH_WRITE.format(
            date_context=date_context(), language_rule=LANGUAGE_RULE, mermaid_rule=MERMAID_RULE,
            passages=_passages(retrieved),
        )
    else:
        system = (
            f"{date_context()}\nNo passage in the user's documents matched this request. Say so "
            f"plainly, suggest how they might rephrase or which document to upload. {LANGUAGE_RULE}"
        )
    response = await graph._llm(with_tools=False, final=True).ainvoke(
        [SystemMessage(content=system), *context, HumanMessage(content=question)]
    )
    answer = response.content or ""

    # 6. Follow-up questions.
    follow_ups: list[str] = []
    if retrieved and answer:
        suggestion = await json_call(
            graph._llm(with_tools=False, json=True),
            [
                SystemMessage(content=RESEARCH_FOLLOW_UPS),
                HumanMessage(content=f"Question: {question}\n\nAnswer: {answer[:1500]}"),
            ],
        )
        follow_ups = [
            q.strip() for q in suggestion.get("questions") or [] if isinstance(q, str) and q.strip()
        ][:3]

    if follow_ups:
        await emit("follow_ups", {"questions": follow_ups})
    return {"messages": [AIMessage(content=answer)], "retrieved": retrieved, "follow_ups": follow_ups}


def _passages(retrieved: dict[str, dict]) -> str:
    hits = sorted(retrieved.values(), key=lambda h: h.get("score") or 0, reverse=True)[:MAX_PASSAGES]
    return "\n\n".join(
        f"[chunk_id: {h['chunk_id']}] {h.get('document_title') or ''}, "
        f"page {h.get('page_start') or '?'}\n{_section_line(h)}{h.get('text') or ''}"
        for h in hits
    )


def _section_line(hit: dict) -> str:
    """Where the passage sits, so the writer can tell same-looking passages apart."""
    section = " ".join((hit.get("section") or "").split())
    context = " ".join((hit.get("context") or "").split())
    line = ". ".join(part for part in (section, context) if part)
    return f"Section: {line}\n" if line else ""
