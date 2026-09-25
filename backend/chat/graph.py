"""The ReAct agent.

    START -> prepare -> agent <-> tools -> finalize -> END

`prepare` bounds the context window before every turn. On a 32GB machine that is
a stability requirement, not an optimization: an unbounded message list will
eventually push num_ctx past what the KV cache can hold and the model stalls.
"""
from __future__ import annotations

import logging
import re
from typing import Annotated, TypedDict

from django.conf import settings
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from chat.research import DEFAULT_RESEARCH_MODE, research_profile
from chat.tools import TOOLS

logger = logging.getLogger(__name__)

SUMMARIZE_AFTER_MESSAGES = 20

SYSTEM_PROMPT = """You are a research assistant for a personal document library. \
The documents were scanned and read by OCR, so the text may contain small errors.

How to answer:
- ALWAYS search the documents before answering a question about their contents. \
Never answer from memory or from general knowledge.
- Answer in the SAME LANGUAGE the user wrote in. If they ask in Arabic, answer in Arabic.
- After each factual sentence drawn from a document, append a citation marker \
in the form [[cite:<chunk_id>]], using a chunk_id you actually received from a tool.
- NEVER cite a chunk_id that a tool did not return to you.
- If the documents do not contain the answer, say so plainly. Do not guess.
- If OCR text looks garbled, say the source may be misread rather than inventing a reading.
- Quote exact figures, dates and reference numbers verbatim; never round or reformat them.
- When the user asks for a graph, diagram, flow, timeline or relationship map and the
documents contain enough evidence, include a valid fenced ```mermaid diagram after a
cited prose explanation. Keep labels concise, use no HTML or external links, and never
put [[cite:...]] markers inside the Mermaid block. Do not add a diagram when prose or a
small table would communicate the answer more clearly.
{scope_note}"""

GENERAL_SYSTEM_PROMPT = """You are a helpful, knowledgeable assistant.

How to answer:
- Answer in the SAME LANGUAGE the user wrote in. If they ask in Arabic, answer in Arabic.
- Answer from your general knowledge. Be accurate and concise, and say so when you are \
unsure rather than guessing.
- In this mode you have NO access to the user's document library. If the user asks about \
their own documents or files, say that you cannot see them here and suggest switching to \
Documents mode.
- Never write citation markers such as [[cite:...]].
- When the user asks for a graph, diagram, flow, timeline or relationship map, you may include
a valid fenced ```mermaid diagram with concise labels, no HTML and no external links."""

SCOPE_ALL = "\nYou are searching the user's entire document library."
SCOPE_SELECTED = (
    "\nYou are scoped to {count} specific document(s): {titles}. Every document "
    "tool is already restricted to those sources; do not claim to consult anything else."
)

CITE_RE = re.compile(r"\[\[cite:([0-9a-fA-F\-]{6,40})\]\]")
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    doc_ids: list[str] | None
    scope_note: str
    research_mode: str
    chat_mode: str
    tool_rounds: int
    retrieved: dict
    summary: str


def _llm(*, with_tools: bool = True):
    from langchain_ollama import ChatOllama

    model = ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=0.2,
        num_ctx=settings.LLM_NUM_CTX,
        # qwen3.5's thinking blocks leak into answers and break citation
        # parsing, so reasoning stays off for this agent.
        reasoning=False,
    )
    return model.bind_tools(TOOLS) if with_tools else model


async def prepare(state: AgentState) -> dict:
    messages = state["messages"]
    updates: dict = {"tool_rounds": 0}

    if len(messages) > SUMMARIZE_AFTER_MESSAGES:
        # Collapse the oldest turns into a summary so num_ctx stays bounded.
        from langchain_ollama import ChatOllama

        summarizer = ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.LLM_MODEL,
            temperature=0,
            num_ctx=settings.LLM_NUM_CTX,
            reasoning=False,
        )
        old = messages[:-8]
        try:
            response = await summarizer.ainvoke(
                [
                    SystemMessage(
                        content="Summarize this conversation in under 200 words. "
                        "Keep document titles, figures and dates exactly."
                    ),
                    HumanMessage(
                        content="\n".join(
                            f"{m.type}: {getattr(m, 'content', '')}" for m in old
                        )[:8000]
                    ),
                ]
            )
            updates["summary"] = response.content
            updates["messages"] = [RemoveMessage(id=m.id) for m in old if m.id]
        except Exception:
            logger.warning("conversation summarization failed", exc_info=True)

    return updates


async def agent(state: AgentState) -> dict:
    if state.get("chat_mode") == "general":
        return await _general_answer(state)

    system = SYSTEM_PROMPT.format(scope_note=state.get("scope_note", SCOPE_ALL))
    profile = research_profile(state.get("research_mode", DEFAULT_RESEARCH_MODE))
    system += f"\n\n{profile['instruction']}"
    if summary := state.get("summary"):
        system += f"\n\nEarlier in this conversation:\n{summary}"

    messages = [SystemMessage(content=system)] + list(state["messages"])
    response = await _llm().ainvoke(messages)
    return {"messages": [response]}


async def _general_answer(state: AgentState) -> dict:
    """Plain LLM turn: no tools are bound, so _route always ends the turn."""
    system = GENERAL_SYSTEM_PROMPT
    if summary := state.get("summary"):
        system += f"\n\nEarlier in this conversation:\n{summary}"

    messages = [SystemMessage(content=system)] + list(state["messages"])
    response = await _llm(with_tools=False).ainvoke(messages)
    return {"messages": [response]}


def _route(state: AgentState) -> str:
    wants_tool = tools_condition(state) == "tools"
    if not wants_tool:
        return "finalize"

    # Each profile has a deterministic cap. If the model asks for another tool
    # at the cap, a tool-free model turn must still produce a useful answer.
    profile = research_profile(state.get("research_mode", DEFAULT_RESEARCH_MODE))
    if state.get("tool_rounds", 0) >= profile["max_tool_rounds"]:
        logger.info("%s research cap reached; forcing answer", state.get("research_mode"))
        return "force_answer"
    return "tools"


async def force_answer(state: AgentState) -> dict:
    """Produce a final cited response instead of ending on an unexecuted tool call."""
    system = SYSTEM_PROMPT.format(scope_note=state.get("scope_note", SCOPE_ALL))
    system += (
        "\n\nThe research budget is exhausted. Do not request more tools. Answer now "
        "using only the evidence already returned, retain exact figures, and include "
        "the required citation markers."
    )
    if summary := state.get("summary"):
        system += f"\n\nEarlier in this conversation:\n{summary}"

    history = list(state["messages"])
    if history and getattr(history[-1], "tool_calls", None):
        history = history[:-1]
    response = await _llm(with_tools=False).ainvoke(
        [SystemMessage(content=system)] + history
    )
    return {"messages": [response]}


async def collect(state: AgentState) -> dict:
    """Accumulate every chunk any tool returned, for citation resolution."""
    retrieved = dict(state.get("retrieved") or {})
    for message in reversed(state["messages"]):
        if message.type != "tool":
            continue
        content = message.content
        if isinstance(content, str):
            try:
                import json

                content = json.loads(content)
            except Exception:
                continue
        if isinstance(content, list):
            for hit in content:
                if isinstance(hit, dict) and hit.get("chunk_id"):
                    retrieved[str(hit["chunk_id"])] = hit
    return {
        "retrieved": retrieved,
        "tool_rounds": state.get("tool_rounds", 0) + 1,
    }


async def finalize(state: AgentState) -> dict:
    return {}


def build_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    graph.add_node("collect", collect)
    graph.add_node("force_answer", force_answer)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "agent")
    graph.add_conditional_edges(
        "agent",
        _route,
        {"tools": "tools", "force_answer": "force_answer", "finalize": "finalize"},
    )
    graph.add_edge("tools", "collect")
    graph.add_edge("collect", "agent")
    graph.add_edge("force_answer", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    """Compiled graph, with memory if the checkpointer pool is up."""
    global _graph
    from chat.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    if _graph is None or getattr(_graph, "checkpointer", None) is not checkpointer:
        _graph = build_graph(checkpointer)
    return _graph


def resolve_citations(text: str, retrieved: dict) -> tuple[str, list[dict], str]:
    """Turn [[cite:id]] markers into [1], [2] and drop hallucinated ids.

    Returns (display_text, citations, mode).
    """
    text = THINK_RE.sub("", text or "").strip()
    ids = CITE_RE.findall(text)

    ordered: list[str] = []
    for chunk_id in ids:
        if chunk_id in retrieved and chunk_id not in ordered:
            ordered.append(chunk_id)

    if ordered:
        numbering = {chunk_id: i + 1 for i, chunk_id in enumerate(ordered)}

        def replace(match):
            chunk_id = match.group(1)
            # An id the model never received is a fabricated citation: remove it
            # rather than rendering a reference that goes nowhere.
            return f"[{numbering[chunk_id]}]" if chunk_id in numbering else ""

        display = CITE_RE.sub(replace, text)
        citations = [
            _citation(retrieved[chunk_id], index + 1)
            for index, chunk_id in enumerate(ordered)
        ]
        return display.strip(), citations, "explicit"

    # No usable markers. Present the retrieved passages as "sources consulted"
    # and label the mode honestly rather than implying precision we don't have.
    display = CITE_RE.sub("", text).strip()
    citations = [
        _citation(hit, index + 1) for index, hit in enumerate(list(retrieved.values())[:5])
    ]
    return display, citations, "implicit" if citations else "none"


def _citation(hit: dict, number: int) -> dict:
    return {
        "n": number,
        "chunk_id": hit.get("chunk_id"),
        "document_id": hit.get("document_id"),
        "title": hit.get("document_title"),
        "page_start": hit.get("page_start"),
        "page_end": hit.get("page_end"),
        "quote": (hit.get("text") or "")[:240],
        "score": hit.get("score"),
    }
