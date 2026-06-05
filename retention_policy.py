from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


VALID_DECISION_MODES = {"all", "balanced", "strict"}
VALID_MEMORY_TYPES = {"preference", "profile", "project", "rule", "correction", "conversation"}
VALID_SENSITIVITY = {"low", "personal", "high"}
GROUP_SCOPE_TYPES = {"group_shared", "group_member"}
DEFAULT_AI_MIN_CONFIDENCE = 0.7
DEFAULT_DEDUPE_THRESHOLD = 0.85


@dataclass(frozen=True)
class RetainDecision:
    should_retain: bool
    reason: str
    sensitivity: str
    keep_user: bool
    keep_assistant: bool
    target_scope_types: tuple[str, ...]
    memory_text: str = ""
    memory_type: str = "conversation"
    confidence: float = 1.0
    source: str = "rules"


def decide_retention(
    user_text: str,
    assistant_text: str,
    primary_scope_type: str,
    config: Any,
) -> RetainDecision:
    user = _clean_text(user_text)
    assistant = _clean_text(assistant_text)
    if not user and not assistant:
        return _skip("empty")

    keep_user = _bool_config(config, "retain_user_message", True) and bool(user)
    keep_assistant = _bool_config(config, "retain_assistant_message", True) and bool(assistant)
    if not keep_user and not keep_assistant:
        return _skip("disabled_content")

    combined = "\n".join(part for part in (user, assistant) if part)
    if _has_hard_sensitive_secret(combined):
        return _skip("hard_sensitive", sensitivity="high")

    mode = _decision_mode(config)
    sensitivity = "personal" if _has_personal_sensitive(user) else "low"

    if mode == "all":
        return RetainDecision(
            should_retain=True,
            reason="all_mode",
            sensitivity=sensitivity,
            keep_user=keep_user,
            keep_assistant=keep_assistant,
            target_scope_types=_target_scope_types(primary_scope_type, user, broad=True),
            memory_type="conversation",
        )

    if _is_command(user):
        return _skip("command")
    if _assistant_is_non_retainable(assistant):
        return _skip("assistant_non_retainable")

    explicit = _has_explicit_memory_intent(user)
    if _has_personal_sensitive(user) and _bool_config(config, "retain_sensitive_requires_explicit", True) and not explicit:
        return _skip("personal_sensitive_requires_explicit", sensitivity="personal")

    if mode == "strict" and not explicit:
        return _skip("strict_requires_explicit", sensitivity=sensitivity)

    min_chars = _retain_min_chars(config)
    if not explicit and _is_low_information(user, min_chars):
        return _skip("low_information", sensitivity=sensitivity)
    if not explicit and _is_chitchat(user):
        return _skip("chitchat", sensitivity=sensitivity)

    stable = _has_stable_fact(user)
    if not explicit and not stable:
        return _skip("not_memorable", sensitivity=sensitivity)

    memory_type = _memory_type(user)
    target_scope_types = _target_scope_types(primary_scope_type, user, broad=False)
    memory_text = _rule_memory_text(user, assistant, target_scope_types, memory_type, min_chars)
    if not memory_text:
        return _skip("empty_memory_text", sensitivity=sensitivity)

    return RetainDecision(
        should_retain=True,
        reason="explicit_memory" if explicit else "stable_fact",
        sensitivity=sensitivity,
        keep_user=keep_user,
        keep_assistant=keep_assistant and _assistant_has_memory_value(assistant, min_chars),
        target_scope_types=target_scope_types,
        memory_text=memory_text,
        memory_type=memory_type,
        source="rules",
    )


def build_ai_retention_prompt(user_text: str, assistant_text: str, primary_scope_type: str) -> str:
    allowed_scopes = ["private"] if primary_scope_type not in GROUP_SCOPE_TYPES else ["group_member", "group_shared"]
    payload = {
        "primary_scope_type": primary_scope_type,
        "allowed_scopes": allowed_scopes,
        "user_text": _clean_text(user_text),
        "assistant_text": _clean_text(assistant_text),
    }
    return (
        "Decide whether this chat turn should be stored as long-term memory.\n"
        "Return one JSON object only. Do not include markdown.\n"
        "Schema: {\n"
        '  "should_retain": boolean,\n'
        '  "memory_text": string,\n'
        '  "scope": "private|group_member|group_shared",\n'
        '  "confidence": number,\n'
        '  "reason": "preference|profile|project|rule|correction|not_memorable|sensitive|duplicate_risk",\n'
        '  "sensitivity": "low|personal|high"\n'
        "}\n"
        "Write memory_text as a concise, standalone fact. Prefer Chinese if the input is Chinese.\n"
        "Never retain API keys, tokens, passwords, private keys, or secrets.\n"
        "Use group_shared only for group rules, announcements, or shared project/team facts.\n"
        "Use group_member for a specific user's preferences, name, profile, or personal context.\n"
        f"Input JSON: {json.dumps(payload, ensure_ascii=False)}"
    )


def apply_ai_retention_result(
    base_decision: RetainDecision,
    response_text: str,
    primary_scope_type: str,
    config: Any,
) -> RetainDecision | None:
    data = _extract_json_object(response_text)
    if data is None:
        return None

    sensitivity = _normalize_sensitivity(data.get("sensitivity"), base_decision.sensitivity)
    confidence = _clamp_float(data.get("confidence"), base_decision.confidence)
    if not _bool_value(data.get("should_retain")):
        return _skip("ai_rejected", sensitivity=sensitivity)
    if confidence < _ai_min_confidence(config):
        return _skip("ai_low_confidence", sensitivity=sensitivity)

    memory_text = _clean_text(data.get("memory_text", ""))
    if not memory_text:
        return _skip("ai_empty_memory_text", sensitivity=sensitivity)
    if _has_hard_sensitive_secret(memory_text):
        return _skip("hard_sensitive", sensitivity="high")
    if _has_personal_sensitive(memory_text) and _bool_config(config, "retain_sensitive_requires_explicit", True):
        if base_decision.reason != "explicit_memory":
            return _skip("personal_sensitive_requires_explicit", sensitivity="personal")

    target_scope_types = _ai_target_scope_types(data.get("scope"), primary_scope_type, base_decision.target_scope_types)
    memory_type = _normalize_memory_type(data.get("reason"), base_decision.memory_type)
    return RetainDecision(
        should_retain=True,
        reason=f"ai_{memory_type}",
        sensitivity=sensitivity,
        keep_user=base_decision.keep_user,
        keep_assistant=base_decision.keep_assistant,
        target_scope_types=target_scope_types,
        memory_text=memory_text,
        memory_type=memory_type,
        confidence=confidence,
        source="ai",
    )


def should_write_raw_conversation(decision: RetainDecision, config: Any) -> bool:
    if decision.reason == "all_mode":
        return True
    return _bool_config(config, "retain_write_raw_conversation", False)


def dedupe_action(candidate_text: str, existing_texts: list[str], threshold: float, memory_type: str) -> str:
    if _normalize_memory_type(memory_type, "conversation") == "correction":
        return "correction"
    best = best_memory_similarity(candidate_text, existing_texts)
    if best >= _clamp_float(threshold, DEFAULT_DEDUPE_THRESHOLD):
        return "duplicate"
    if best >= 0.55:
        return "supplement"
    return "new"


def best_memory_similarity(candidate_text: str, existing_texts: list[str]) -> float:
    candidate = _similarity_text(candidate_text)
    if not candidate:
        return 0.0
    scores = []
    for existing in existing_texts:
        normalized = _similarity_text(existing)
        if normalized:
            scores.append(SequenceMatcher(None, candidate, normalized).ratio())
    return max(scores, default=0.0)


def _skip(reason: str, *, sensitivity: str = "low") -> RetainDecision:
    return RetainDecision(
        should_retain=False,
        reason=reason,
        sensitivity=sensitivity,
        keep_user=False,
        keep_assistant=False,
        target_scope_types=(),
    )


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _decision_mode(config: Any) -> str:
    mode = str(_config_get(config, "retain_decision_mode", "balanced") or "balanced").strip().lower()
    if mode not in VALID_DECISION_MODES:
        return "balanced"
    return mode


def _retain_min_chars(config: Any) -> int:
    try:
        return max(1, int(_config_get(config, "retain_min_chars", 8) or 8))
    except (TypeError, ValueError):
        return 8


def _ai_min_confidence(config: Any) -> float:
    return _clamp_float(_config_get(config, "retain_ai_min_confidence", DEFAULT_AI_MIN_CONFIDENCE), DEFAULT_AI_MIN_CONFIDENCE)


def _config_get(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def _bool_config(config: Any, key: str, default: bool) -> bool:
    value = _config_get(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _clamp_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _clean_text(text)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _is_command(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(("/", "!", "！"))


def _is_low_information(text: str, min_chars: int) -> bool:
    compact = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return len(compact) < min_chars


def _is_chitchat(text: str) -> bool:
    compact = re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)
    if compact in {
        "ok",
        "okay",
        "thanks",
        "thankyou",
        "thx",
        "好的",
        "好",
        "嗯",
        "嗯嗯",
        "谢谢",
        "感谢",
        "辛苦了",
        "哈哈",
        "哈哈哈",
    }:
        return True
    return bool(
        re.fullmatch(
            r"(谢谢|感谢|好的|好呀|可以|收到|明白|了解|ok|okay|thanks|thank you)[啊呀呢啦\w]*",
            text.strip().lower(),
        )
    )


def _has_explicit_memory_intent(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"记住",
        r"请记",
        r"帮我记",
        r"以后.*(叫我|称呼|不要|别|用|回复)",
        r"叫我",
        r"称呼我",
        r"我的偏好",
        r"我偏好",
        r"我喜欢",
        r"我不喜欢",
        r"不要再",
        r"别再",
        r"群规",
        r"群公告",
        r"群约定",
        r"项目约定",
        r"remember",
        r"call me",
        r"my preference",
        r"\bi prefer\b",
        r"\bi like\b",
        r"\bi don'?t like\b",
        r"do not .* again",
        r"don'?t .* again",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _has_stable_fact(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"我叫",
        r"我是",
        r"我的(项目|仓库|技术栈|环境|系统|语言|习惯|需求|目标|名字|昵称)",
        r"我(喜欢|不喜欢|偏好|常用|主要用|正在做|在做)",
        r"我们(项目|团队|仓库|技术栈|使用|采用|约定|主要用)",
        r"本群",
        r"群规",
        r"规则是",
        r"约定是",
        r"项目.*(使用|采用|是|叫)",
        r"仓库.*(使用|采用|是|叫)",
        r"技术栈",
        r"不是.*是",
        r"不对.*是",
        r"纠正",
        r"\bmy name is\b",
        r"\bcall me\b",
        r"\bmy (project|repo|repository|stack|preference|nickname|name)\b",
        r"\bi (prefer|like|don'?t like|usually use|am working on)\b",
        r"\bwe (use|prefer|decided|agreed)\b",
        r"\bour (project|repo|repository|stack|rule|agreement)\b",
        r"\bactually\b",
        r"\bcorrection\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _has_hard_sensitive_secret(text: str) -> bool:
    patterns = (
        r"(?i)\b(api[_ -]?key|token|password|passwd|secret)\b\s*[:：=是为]\s*\S+",
        r"(?i)\bbearer\s+[a-z0-9._~+/\-]{8,}",
        r"(?i)\bsk-[a-z0-9_\-]{8,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(密码|密钥|令牌)\s*[:：=是为]\s*\S+",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _has_personal_sensitive(text: str) -> bool:
    patterns = (
        r"(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b",
        r"\b1[3-9]\d{9}\b",
        r"\b\d{15}\b",
        r"\b\d{17}[\dXx]\b",
        r"(手机号|电话号码|身份证|邮箱|email|address|地址)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _assistant_is_non_retainable(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    patterns = (
        r"抱歉.*(无法|不能|做不到)",
        r"无法.*(完成|处理|回答|提供)",
        r"不能.*(完成|处理|回答|提供)",
        r"出错",
        r"失败",
        r"错误",
        r"\bi'?m sorry\b",
        r"\bi can'?t\b",
        r"\bi cannot\b",
        r"\bas an ai\b",
        r"\berror\b",
        r"\bfailed\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _assistant_has_memory_value(text: str, min_chars: int) -> bool:
    if not text or _is_low_information(text, min_chars) or _is_chitchat(text):
        return False
    acknowledgement_patterns = (
        r"^(好的|好|已记住|我记住了|记住了|了解|收到|明白)[。！!\s]*$",
        r"^(ok|okay|got it|noted|remembered)[.!?\s]*$",
    )
    lowered = text.strip().lower()
    return not any(re.search(pattern, lowered) for pattern in acknowledgement_patterns)


def _target_scope_types(primary_scope_type: str, text: str, *, broad: bool) -> tuple[str, ...]:
    if primary_scope_type not in GROUP_SCOPE_TYPES:
        return ("private",)
    if broad:
        return ("group_shared", "group_member")
    if _is_group_public_fact(text) and not _is_personal_fact(text):
        return ("group_shared",)
    return ("group_member",)


def _ai_target_scope_types(value: Any, primary_scope_type: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    scope = str(value or "").strip().lower()
    if primary_scope_type not in GROUP_SCOPE_TYPES:
        return ("private",)
    if scope in GROUP_SCOPE_TYPES:
        return (scope,)
    return fallback or ("group_member",)


def _is_group_public_fact(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"本群",
        r"群规",
        r"群公告",
        r"群约定",
        r"大家",
        r"所有人",
        r"公共",
        r"团队约定",
        r"项目约定",
        r"群里",
        r"\bgroup rule\b",
        r"\bteam rule\b",
        r"\beveryone\b",
        r"\bannouncement\b",
        r"\bour team\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _is_personal_fact(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        r"我(?!们)",
        r"我的",
        r"叫我",
        r"称呼我",
        r"\bmy\b",
        r"\bi\b",
        r"\bme\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _memory_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"不是.*是|不对.*是|纠正|\bactually\b|\bcorrection\b", lowered):
        return "correction"
    if re.search(r"群规|规则|约定|公告|rule|agreement|announcement", lowered):
        return "rule"
    if re.search(r"项目|仓库|技术栈|repo|repository|project|stack", lowered):
        return "project"
    if re.search(r"叫我|称呼|我叫|名字|昵称|my name|call me|nickname", lowered):
        return "profile"
    if re.search(r"喜欢|不喜欢|偏好|不要再|别再|prefer|like|don't like|do not", lowered):
        return "preference"
    return "conversation"


def _normalize_memory_type(value: Any, default: str) -> str:
    memory_type = str(value or "").strip().lower()
    aliases = {
        "stable_user_preference": "preference",
        "user_preference": "preference",
        "group_rule": "rule",
        "sensitive": "conversation",
        "duplicate_risk": "conversation",
        "not_memorable": "conversation",
    }
    memory_type = aliases.get(memory_type, memory_type)
    if memory_type not in VALID_MEMORY_TYPES:
        return default if default in VALID_MEMORY_TYPES else "conversation"
    return memory_type


def _normalize_sensitivity(value: Any, default: str) -> str:
    sensitivity = str(value or "").strip().lower()
    if sensitivity not in VALID_SENSITIVITY:
        return default if default in VALID_SENSITIVITY else "low"
    return sensitivity


def _rule_memory_text(
    user: str,
    assistant: str,
    target_scope_types: tuple[str, ...],
    memory_type: str,
    min_chars: int,
) -> str:
    text = _strip_memory_intent(user)
    if target_scope_types == ("group_shared",):
        memory = text
    else:
        memory = _personalize_user_fact(text)
    if _assistant_has_memory_value(assistant, min_chars):
        memory = f"{memory}\nAssistant context: {_normalize_spaces(assistant)}"
    return _normalize_spaces(memory)


def _strip_memory_intent(text: str) -> str:
    stripped = _normalize_spaces(text)
    stripped = re.sub(r"^(请|麻烦)?(帮我)?(记住|记录一下|记一下|请记住)[:：,，\s]*", "", stripped)
    stripped = re.sub(r"^(remember|please remember|note this)[:：,，\s]*", "", stripped, flags=re.IGNORECASE)
    return stripped.strip()


def _personalize_user_fact(text: str) -> str:
    value = text.strip()
    replacements = (
        (r"^我的", "用户的"),
        (r"^我(?!们)", "用户"),
        (r"^叫我(.+)$", r"用户希望被称呼为\1"),
        (r"^称呼我(.+)$", r"用户希望被称呼为\1"),
        (r"(?i)^my ", "User's "),
        (r"(?i)^i prefer ", "User prefers "),
        (r"(?i)^i like ", "User likes "),
        (r"(?i)^i do not like ", "User does not like "),
        (r"(?i)^i don't like ", "User does not like "),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value)
    return value


def _normalize_spaces(text: str) -> str:
    return " ".join(str(text or "").split())


def _similarity_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())
