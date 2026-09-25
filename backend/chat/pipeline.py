"""Shared plumbing for the multi-step chat pipelines (deep think, deep research)."""
from __future__ import annotations

import json
import logging
import re

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import AnyMessage, BaseMessage

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


async def emit(name: str, data: dict) -> None:
    """Progress for the UI. Surfaces in astream_events as `on_custom_event`."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # Outside a graph run (e.g. a unit test calling a helper directly).
        logger.debug("custom event %s dropped: no parent run", name)


async def json_call(model, messages: list[BaseMessage]) -> dict:
    """Invoke a JSON-mode model and parse its reply; {} when it will not parse."""
    try:
        response = await model.ainvoke(messages)
    except Exception:
        logger.warning("structured pipeline call failed", exc_info=True)
        return {}
    raw = _FENCE_RE.sub("", (response.content or "").strip())
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("pipeline JSON did not parse: %s", raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def text_of(message: AnyMessage) -> str:
    """The text of a message, skipping image parts (never leak base64 into prompts)."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif isinstance(part, dict) and part.get("type") == "image_url":
            parts.append("[image]")
    return "\n".join(parts)


def last_user_request(messages: list[AnyMessage]) -> AnyMessage | None:
    for message in reversed(messages):
        if message.type == "human":
            return message
    return None
