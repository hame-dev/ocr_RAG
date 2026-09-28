"""Deep think: plan → work through sub-questions → review → write.

The structure of the docvision-rag deep researcher, pointed at the model's own
knowledge (plus any attached files) instead of a search index.

Where the model's (slow) built-in thinking is spent matters on a local 9B
model. Measured live: thinking on every step, plus re-working every step the
reviewer flagged, took ~10 minutes. So:
  - working steps write short notes with thinking off; the notes stream to the
    UI's thinking panel as the visible reasoning trail;
  - the reviewer's findings go to the writer to fix, instead of re-running steps;
  - thinking is spent once, in the final write, over the consolidated notes.
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chat.pipeline import emit, json_call, last_user_request, text_of
from chat.prompts import (
    LANGUAGE_RULE, MERMAID_RULE, THINK_PLAN, THINK_REVIEW, THINK_WORK, THINK_WRITE, date_context,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 3
NOTES_TOKENS = 450
NOTES_CHARS = 2000


async def deep_think(state) -> dict:
    from chat import graph  # lazy: graph imports this module

    history = list(state["messages"])
    request = last_user_request(history)
    request_text = text_of(request) if request else ""
    context = graph.history_context(state)
    turn = [*context, request] if request else [*context]

    # 1. Plan.
    await emit("phase", {"phase": "planning"})
    plan = await json_call(
        graph._llm(with_tools=False, json=True),
        [SystemMessage(content=THINK_PLAN.format(date_context=date_context(), max_steps=MAX_STEPS)), *turn],
    )
    goal = str(plan.get("goal") or request_text[:200])
    steps = [
        str(step.get("question")).strip()
        for step in plan.get("steps") or []
        if isinstance(step, dict) and str(step.get("question") or "").strip()
    ][:MAX_STEPS] or [request_text or goal]
    await emit("phase", {"phase": "planned", "detail": goal, "total": len(steps), "steps": steps})

    # 2. Work through each sub-question: short notes, streamed as the trail.
    notes: list[str] = []
    for index, question in enumerate(steps):
        await emit("phase", {"phase": "working", "index": index + 1, "total": len(steps), "detail": question})
        try:
            response = await graph._llm(with_tools=False, max_tokens=NOTES_TOKENS).ainvoke([
                SystemMessage(content=THINK_WORK.format(
                    date_context=date_context(), goal=goal, language_rule=LANGUAGE_RULE,
                )),
                # The original request carries any attached images and files.
                *turn,
                HumanMessage(content=f"Work on this part now: {question}"),
            ])
            note = (response.content or "").strip()[:NOTES_CHARS]
        except Exception:
            logger.warning("deep think step %s failed", index + 1, exc_info=True)
            note = ""
        notes.append(note)
        if note:
            await emit("note", {"index": index + 1, "question": question, "text": note})

    # 3. Review. Findings go to the writer rather than re-running steps.
    await emit("phase", {"phase": "reviewing"})
    review = await json_call(
        graph._llm(with_tools=False, json=True),
        [
            SystemMessage(content=THINK_REVIEW),
            HumanMessage(content=f"Request: {request_text}\n\n" + _format_notes(steps, notes)),
        ],
    )
    issues = [
        f"- Step {issue.get('step')}: {issue.get('problem')}"
        for issue in review.get("issues") or []
        if isinstance(issue, dict) and issue.get("problem")
    ]

    # 4. Write, with thinking on (the one place it is spent). Streamed: the
    # only call tagged as the final answer.
    await emit("phase", {"phase": "writing"})
    system = THINK_WRITE.format(
        date_context=date_context(),
        language_rule=LANGUAGE_RULE,
        mermaid_rule=MERMAID_RULE,
        notes=_format_notes(steps, notes),
        issues="\n".join(issues) or "(none)",
    )
    response = await graph._llm(with_tools=False, reasoning=True, final=True).ainvoke(
        [SystemMessage(content=system), *turn]
    )
    return {"messages": [AIMessage(content=response.content or "")]}


def _format_notes(steps: list[str], notes: list[str]) -> str:
    return "\n\n".join(
        f"Step {i + 1}: {question}\nNotes: {note or '(no notes)'}"
        for i, (question, note) in enumerate(zip(steps, notes))
    )
