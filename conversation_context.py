from __future__ import annotations

import re
from typing import Any


DEFAULT_CONTEXT_TURNS = 3
DEFAULT_MESSAGE_MAX_CHARS = 300
SHORT_QUERY_MAX_CHARS = 12
CONTEXT_ROLES = {"user", "assistant"}


def recent_turns(
    contexts: Any,
    max_turns: int = DEFAULT_CONTEXT_TURNS,
    max_chars_per_message: int = DEFAULT_MESSAGE_MAX_CHARS,
) -> list[dict[str, str]]:
    """Return the last user/assistant text messages from OpenAI-format contexts.

    One turn is roughly one user message plus one assistant reply, so this keeps
    at most ``max_turns * 2`` messages. System, tool and tool-call-only entries
    are dropped.
    """
    if max_turns <= 0 or not isinstance(contexts, list):
        return []
    messages: list[dict[str, str]] = []
    for item in contexts:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in CONTEXT_ROLES:
            continue
        text = _content_text(item.get("content"))
        if not text:
            continue
        messages.append({"role": role, "text": _truncate(text, max_chars_per_message)})
    return messages[-max_turns * 2 :]


def short_query_with_context(query: str, recent: list[dict[str, str]]) -> str:
    """Build an extra recall query for short follow-ups such as "那个呢？".

    Returns "" when the query is long enough to stand on its own or there is
    no earlier user message to borrow from.
    """
    compact = re.sub(r"[\s\W_]+", "", str(query or ""), flags=re.UNICODE)
    if not compact or len(compact) >= SHORT_QUERY_MAX_CHARS:
        return ""
    for message in reversed(recent):
        if message.get("role") == "user" and message.get("text"):
            return f"{message['text']} {str(query).strip()}"
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return _normalize(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return _normalize(" ".join(parts))
    return ""


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"
