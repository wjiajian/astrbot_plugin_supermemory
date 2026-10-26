from __future__ import annotations

from datetime import datetime
import re
from typing import Any


DEFAULT_ITEM_MAX_CHARS = 360
MAX_EXTRACT_DEPTH = 4
RRF_K = 60
DATE_KEYS = ("date", "createdAt", "created_at", "updatedAt", "updated_at")
GUIDANCE_LINE = (
    "以下是关于当前用户/群的长期记忆，按需自然参考，不要逐条复述；"
    "与用户当前说法冲突时以当前说法为准；与当前话题无关则忽略。"
)


def format_recall_results(
    raw: Any,
    limit: int,
    item_max_chars: int = DEFAULT_ITEM_MAX_CHARS,
    title: str = "memory",
    max_extract_depth: int = MAX_EXTRACT_DEPTH,
) -> str:
    memories = extract_memories(raw)
    if not memories:
        return ""

    lines: list[str] = []
    for memory in memories[: max(0, limit)]:
        text = _extract_text(memory, max_depth=max_extract_depth)
        if not text:
            continue
        lines.append(f"- {_memory_prefix(memory)}{_truncate(_normalize_text(text), item_max_chars)}")

    if not lines:
        return ""
    return f'<supermemory_context scope="{title}">\n' + "\n".join(lines) + "\n</supermemory_context>"


def _memory_prefix(memory: Any) -> str:
    if not isinstance(memory, dict):
        return ""
    for key in DATE_KEYS:
        date = _memory_date(memory.get(key))
        if date:
            return f"[{date}] "
    return ""


def _memory_date(value: Any) -> str:
    """Return the local YYYY-MM-DD of an ISO 8601 timestamp, or "" if unparseable."""
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return match.group(1) if match else ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.date().isoformat()


def extract_memories(raw: Any) -> list[Any]:
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


def extract_memory_texts(raw: Any, limit: int = 0, max_extract_depth: int = MAX_EXTRACT_DEPTH) -> list[str]:
    memories = extract_memories(raw)
    if limit > 0:
        memories = memories[:limit]
    texts: list[str] = []
    for memory in memories:
        text = _extract_text(memory, max_depth=max_extract_depth)
        if text:
            texts.append(_normalize_text(text))
    return texts


def merge_ranked_memories(
    ranked_lists: list[list[Any]],
    max_extract_depth: int = MAX_EXTRACT_DEPTH,
    rrf_k: int = RRF_K,
) -> list[Any]:
    """Merge several ranked search result lists with reciprocal rank fusion.

    Each list comes from one query against the same scope. Concatenating them
    would let the first query fill the whole injection budget; RRF instead
    favors memories ranked high in any list and memories returned by several
    queries.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, Any]] = {}
    order = 0
    for memories in ranked_lists:
        seen_in_list: set[str] = set()
        for rank, memory in enumerate(memories):
            text = _extract_text(memory, max_depth=max_extract_depth)
            key = _normalize_text(text).lower()
            if not key or key in seen_in_list:
                continue
            seen_in_list.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank + 1)
            if key not in first_seen:
                first_seen[key] = (order, memory)
                order += 1
    ranked_keys = sorted(scores, key=lambda key: (-scores[key], first_seen[key][0]))
    return [first_seen[key][1] for key in ranked_keys]


def format_search_results(
    raw: Any,
    limit: int,
    item_max_chars: int = DEFAULT_ITEM_MAX_CHARS,
    title: str = "memory",
    max_extract_depth: int = MAX_EXTRACT_DEPTH,
) -> str:
    return format_recall_results(raw, limit, item_max_chars, title, max_extract_depth)


def extract_results(raw: Any) -> list[Any]:
    return extract_memories(raw)


def _extract_text(
    memory: Any,
    depth: int = 0,
    seen: set[int] | None = None,
    max_depth: int = MAX_EXTRACT_DEPTH,
) -> str:
    if depth > max_depth:
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
            text = _extract_text(nested, depth + 1, seen, max_depth)
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
