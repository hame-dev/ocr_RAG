"""The ReAct agent.

    START -> prepare -> agent <-> tools -> finalize -> END
                     +-> deep_think ------------+      (General, "Deep think")
                     +-> deep_research ---------+      (Documents, "Deep research")

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

from chat.deep_research import deep_research
from chat.deep_think import deep_think
from chat.pipeline import text_of
from chat.research import DEFAULT_RESEARCH_MODE, research_profile
from chat.tools import TOOLS

logger = logging.getLogger(__name__)

SUMMARIZE_AFTER_MESSAGES = 20

SYSTEM_PROMPT = """You are a research assistant for a personal document library. \
The documents were scanned and read by OCR, so the text may contain small errors.

How to answer:
- ALWAYS search the documents before answering a question about their contents. \
Never answer from memory or from general knowledge.
- Answer in the language of the user's OWN message, never the language of a document, attachment or search result: an English question about an Arabic document gets an English answer, and an Arabic question gets an Arabic answer.
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
- Answer in the language of the user's OWN message, never the language of a document, attachment or search result: an English question about an Arabic document gets an English answer, and an Arabic question gets an Arabic answer.
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
    thinking: str
    tool_rounds: int
    retrieved: dict
    summary: str
    follow_ups: list[str]


# Tag carried by every model call whose output IS the user-visible answer.
# chat_stream streams only these tokens; planning/checking calls stay silent.
FINAL_ANSWER_TAG = "final_answer"


def _llm(
    *,
    with_tools: bool = True,
    reasoning: bool = False,
    json: bool = False,
    final: bool = False,
    max_tokens: int | None = None,
):
    """The chat model.

    reasoning: qwen's thinking. langchain-ollama returns it separately in
        additional_kwargs["reasoning_content"], so it never mixes into the
        answer text (inline <think> blocks used to break citation parsing).
    json: Ollama's JSON mode, for the pipelines' structured steps.
    final: tag the call as producing the user-visible answer.
    max_tokens: cap the generated length (Ollama num_predict).
    """
    from langchain_ollama import ChatOllama

    model = ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=0.1 if json else 0.2,
        num_ctx=settings.LLM_NUM_CTX,
        reasoning=reasoning,
        **({"format": "json"} if json else {}),
        **({"num_predict": max_tokens} if max_tokens else {}),
    )
    runnable = model.bind_tools(TOOLS) if with_tools else model
    return runnable.with_config(tags=[FINAL_ANSWER_TAG]) if final else runnable


HISTORY_TURNS = 6


def history_context(state: AgentState) -> list:
    """Earlier turns for the multi-step pipelines, without the current request.

    Tool traffic is dropped (the pipelines call the model without tools bound),
    but earlier user messages keep their attachments, so a file or image shared
    earlier in the conversation stays visible to later turns.
    """
    messages = list(state["messages"])
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].type == "human":
            messages = messages[:index]
            break
    kept = [
        m for m in messages
        if m.type == "human" or (m.type == "ai" and not getattr(m, "tool_calls", None) and m.content)
    ][-HISTORY_TURNS:]
    context = []
    if summary := state.get("summary"):
        context.append(SystemMessage(content=f"Earlier in this conversation:\n{summary}"))
    return context + kept


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
                        # Text parts only: attachments put base64 images in
                        # message content, which must never reach this prompt.
                        content="\n".join(f"{m.type}: {text_of(m)}" for m in old)[:8000]
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
    response = await _llm(final=True).ainvoke(messages)
    return {"messages": [response]}


async def _general_answer(state: AgentState) -> dict:
    """Plain LLM turn: no tools are bound, so _route always ends the turn.

    "Think" turns on the model's own reasoning; it streams to the UI as
    `thinking` events and is kept out of the answer text.
    """
    system = GENERAL_SYSTEM_PROMPT
    if summary := state.get("summary"):
        system += f"\n\nEarlier in this conversation:\n{summary}"

    messages = [SystemMessage(content=system)] + list(state["messages"])
    thinking = state.get("thinking") == "think"
    response = await _llm(with_tools=False, reasoning=thinking, final=True).ainvoke(messages)
    return {"messages": [response]}


def _select_path(state: AgentState) -> str:
    """Which pipeline answers this turn."""
    if state.get("chat_mode") == "general":
        return "deep_think" if state.get("thinking") == "deep" else "agent"
    if state.get("research_mode") == "deep":
        return "deep_research"
    return "agent"


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
    response = await _llm(with_tools=False, final=True).ainvoke(
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
    graph.add_node("deep_think", deep_think)
    graph.add_node("deep_research", deep_research)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare")
    graph.add_conditional_edges(
        "prepare",
        _select_path,
        {"agent": "agent", "deep_think": "deep_think", "deep_research": "deep_research"},
    )
    graph.add_edge("deep_think", "finalize")
    graph.add_edge("deep_research", "finalize")
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
