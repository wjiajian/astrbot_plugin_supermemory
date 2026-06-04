from __future__ import annotations

from typing import Any


DEFAULT_ITEM_MAX_CHARS = 360
MAX_EXTRACT_DEPTH = 4


def format_search_results(
    raw: Any,
    limit: int,
    item_max_chars: int = DEFAULT_ITEM_MAX_CHARS,
    title: str = "memory",
) -> str:
    memories = extract_results(raw)
    if not memories:
        return ""

    lines: list[str] = []
    for memory in memories[: max(0, limit)]:
        text = _extract_text(memory)
        if not text:
            continue
        lines.append(f"- {_truncate(_normalize_text(text), item_max_chars)}")

    if not lines:
        return ""
    return f'<supermemory_context scope="{title}">\n' + "\n".join(lines) + "\n</supermemory_context>"


def extract_results(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []

    for key in ("results", "memories", "items", "data"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return []


def _extract_text(memory: Any, depth: int = 0, seen: set[int] | None = None) -> str:
    if depth > MAX_EXTRACT_DEPTH:
        return ""
    if isinstance(memory, str):
        return memory
    if not isinstance(memory, dict):
        return ""

    if seen is None:
        seen = set()
    memory_id = id(memory)
    if memory_id in seen:
        return ""
    seen.add(memory_id)

    for key in ("memory", "chunk", "text", "content", "fact", "summary"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value

    for key in ("memory", "observation", "document", "result"):
        nested = memory.get(key)
        if isinstance(nested, dict):
            text = _extract_text(nested, depth + 1, seen)
            if text:
                return text

    return ""


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    return text[: max_chars - 3].rstrip() + "..."
