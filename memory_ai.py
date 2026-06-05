from __future__ import annotations

import json
import re
from typing import Any


DEFAULT_MEMORY_AI_MIN_CONFIDENCE = 0.7
DEFAULT_RECALL_QUERY_LIMIT = 4


def memory_ai_enabled(config: Any) -> bool:
    if _has_config_key(config, "memory_ai_enabled"):
        return _bool_config(config, "memory_ai_enabled", False)
    return _bool_config(config, "retain_ai_enabled", False)


def memory_ai_provider_id(config: Any) -> str:
    provider_id = str(_config_get(config, "memory_ai_provider_id", "") or "").strip()
    if provider_id:
        return provider_id
    return str(_config_get(config, "retain_ai_provider_id", "") or "").strip()


def memory_ai_fallback_to_current_provider(config: Any) -> bool:
    if _has_config_key(config, "memory_ai_fallback_to_current_provider"):
        return _bool_config(config, "memory_ai_fallback_to_current_provider", True)
    return _bool_config(config, "retain_ai_fallback_to_current_provider", True)


def memory_ai_min_confidence(config: Any) -> float:
    if _has_config_key(config, "memory_ai_min_confidence"):
        return _clamp_float(_config_get(config, "memory_ai_min_confidence", DEFAULT_MEMORY_AI_MIN_CONFIDENCE))
    return _clamp_float(_config_get(config, "retain_ai_min_confidence", DEFAULT_MEMORY_AI_MIN_CONFIDENCE))


def build_recall_query_prompt(query: str, max_queries: int = DEFAULT_RECALL_QUERY_LIMIT) -> str:
    payload = {
        "user_query": _clean_text(query),
        "max_queries": max(1, int(max_queries)),
    }
    return (
        "Rewrite the user's memory lookup request into search queries for long-term memory recall.\n"
        "Return one JSON object only. Do not answer the user.\n"
        "Schema: {\"queries\": [\"search query\"], \"reason\": \"short reason\"}\n"
        "Rules:\n"
        "- Include the original meaning, not necessarily the exact original words.\n"
        "- Generate 1 to max_queries short search queries.\n"
        "- Prefer the user's language.\n"
        "- Include terms that help retrieve names, preferences, project facts, rules, corrections, or prior decisions when relevant.\n"
        "- Never include API keys, passwords, tokens, private keys, or secrets.\n"
        f"Input JSON: {json.dumps(payload, ensure_ascii=False)}"
    )


def parse_recall_queries(response_text: str, original_query: str, max_queries: int = DEFAULT_RECALL_QUERY_LIMIT) -> list[str]:
    original = _clean_text(original_query)
    queries = [original] if original else []
    data = _extract_json_object(response_text)
    if isinstance(data, dict):
        raw_queries = data.get("queries", [])
        if isinstance(raw_queries, list):
            queries.extend(str(item) for item in raw_queries)
    return _unique_non_empty(queries, max(1, int(max_queries)))


def _unique_non_empty(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = re.sub(r"\s+", "", text).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _clean_text(text)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = raw.find("{", start)
        if index < 0:
            return None
        try:
            data, _ = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(data, dict):
            return data
        start = index + 1


def _has_config_key(config: Any, key: str) -> bool:
    if isinstance(config, dict):
        return key in config
    return hasattr(config, key)


def _config_get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(config, key, default)


def _bool_config(config: Any, key: str, default: bool) -> bool:
    value = _config_get(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MEMORY_AI_MIN_CONFIDENCE
    return min(1.0, max(0.0, number))


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
