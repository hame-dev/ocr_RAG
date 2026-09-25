from __future__ import annotations

from typing import Literal, TypedDict


ResearchMode = Literal["fast", "balanced", "deep"]
DEFAULT_RESEARCH_MODE: ResearchMode = "balanced"

# "documents" is the cited RAG agent; "general" is the plain LLM with no tools
# and no access to the library. Chosen per message, so one thread can mix both.
ChatMode = Literal["documents", "general"]
DEFAULT_CHAT_MODE: ChatMode = "documents"
CHAT_MODES: tuple[ChatMode, ...] = ("documents", "general")


class ResearchProfile(TypedDict):
    top_k: int
    excerpt_chars: int
    max_tool_rounds: int
    instruction: str


RESEARCH_PROFILES: dict[ResearchMode, ResearchProfile] = {
    "fast": {
        "top_k": 5,
        "excerpt_chars": 500,
        "max_tool_rounds": 2,
        "instruction": (
            "Research effort is FAST. Run one focused document search, then answer "
            "concisely from the strongest evidence. Expand context only when the top "
            "passage is incomplete."
        ),
    },
    "balanced": {
        "top_k": 8,
        "excerpt_chars": 500,
        "max_tool_rounds": 5,
        "instruction": (
            "Research effort is BALANCED. Refine the query or expand a promising "
            "passage when that materially improves the answer. Prefer corroborated, "
            "well-scoped evidence over extra searches."
        ),
    },
    "deep": {
        "top_k": 10,
        "excerpt_chars": 350,
        "max_tool_rounds": 8,
        "instruction": (
            "Research effort is DEEP. Search with at least two meaningfully different "
            "query formulations, compare the results, then verify the strongest evidence "
            "with neighbouring chunks or the original page before answering."
        ),
    },
}


def normalize_research_mode(value: object) -> ResearchMode | None:
    return value if isinstance(value, str) and value in RESEARCH_PROFILES else None


def research_profile(mode: str) -> ResearchProfile:
    normalized = normalize_research_mode(mode) or DEFAULT_RESEARCH_MODE
    return RESEARCH_PROFILES[normalized]


def normalize_chat_mode(value: object) -> ChatMode | None:
    return value if isinstance(value, str) and value in CHAT_MODES else None


# General-mode reasoning effort, chosen per message like the research depth.
#   instant  plain answer, no model thinking
#   think    the model's built-in thinking, streamed to the UI
#   deep     plan → work through sub-questions → review → write (deep_think.py)
ThinkingMode = Literal["instant", "think", "deep"]
DEFAULT_THINKING: ThinkingMode = "instant"
THINKING_MODES: tuple[ThinkingMode, ...] = ("instant", "think", "deep")


def normalize_thinking(value: object) -> ThinkingMode | None:
    return value if isinstance(value, str) and value in THINKING_MODES else None
