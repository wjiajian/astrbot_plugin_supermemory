from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


HASH_LENGTH = 16
MAX_CONTAINER_TAG_LENGTH = 100
MAX_PLATFORM_LENGTH = 24


class MissingScopeIdentityError(ValueError):
    """Raised when an event cannot be mapped to an isolated memory scope."""


@dataclass(frozen=True)
class MemoryScope:
    scope_type: str
    platform_id: str
    umo_hash: str
    container_tag: str
    scope_key: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MemoryScopes:
    primary: MemoryScope
    recall_scopes: list[MemoryScope]
    retain_scopes: list[MemoryScope]


def build_scopes_from_event(event: Any, salt: str) -> MemoryScopes:
    platform_id = _sanitize_tag_value(_event_value(event, "get_platform_name", "platform_id") or "unknown")
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    umo_hash = hash_identifier(salt, "umo", umo or "unknown")
    group_id = _event_value(event, "get_group_id", "group_id")
    sender_id = _sender_id(event)
    if group_id and not sender_id:
        raise MissingScopeIdentityError("group event is missing sender_id; refuse to build group_member scope")
    if not sender_id and not umo:
        raise MissingScopeIdentityError("event is missing sender_id and unified_msg_origin; refuse unknown memory scope")

    sender_hash = hash_identifier(salt, "sender", str(sender_id or umo))
    if group_id:
        group_hash = hash_identifier(salt, "group", str(group_id))
        shared_container_tag = _build_container_tag("group_shared", platform_id, group_hash, umo_hash)
        shared_scope = MemoryScope(
            scope_type="group_shared",
            platform_id=platform_id,
            umo_hash=umo_hash,
            container_tag=shared_container_tag,
            scope_key=shared_container_tag,
            metadata=_metadata("group_shared", platform_id, shared_container_tag),
        )

        member_container_tag = _build_container_tag("group_member", platform_id, group_hash, sender_hash, umo_hash)
        member_scope = MemoryScope(
            scope_type="group_member",
            platform_id=platform_id,
            umo_hash=umo_hash,
            container_tag=member_container_tag,
            scope_key=member_container_tag,
            metadata=_metadata("group_member", platform_id, member_container_tag),
        )
        return MemoryScopes(
            primary=member_scope,
            recall_scopes=[shared_scope, member_scope],
            retain_scopes=[shared_scope, member_scope],
        )

    container_tag = _build_container_tag("private", platform_id, sender_hash, umo_hash)
    private_scope = MemoryScope(
        scope_type="private",
        platform_id=platform_id,
        umo_hash=umo_hash,
        container_tag=container_tag,
        scope_key=container_tag,
        metadata=_metadata("private", platform_id, container_tag),
    )
    return MemoryScopes(
        primary=private_scope,
        recall_scopes=[private_scope],
        retain_scopes=[private_scope],
    )


def build_scope_from_event(event: Any, salt: str) -> MemoryScope:
    return build_scopes_from_event(event, salt).primary


def hash_identifier(salt: str, namespace: str, value: str) -> str:
    data = f"{salt}:{namespace}:{value}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:HASH_LENGTH]


def _build_container_tag(scope_type: str, platform_id: str, *parts: str) -> str:
    platform = platform_id[:MAX_PLATFORM_LENGTH] or "unknown"
    tag = "_".join(("astrbot", scope_type, platform, *parts))
    return tag[:MAX_CONTAINER_TAG_LENGTH]


def _metadata(scope_type: str, platform_id: str, scope_key: str) -> dict[str, Any]:
    return {
        "source": "astrbot_plugin_supermemory",
        "scope": scope_type,
        "platform_id": platform_id,
        "scope_key": scope_key,
    }


def _event_value(event: Any, method_name: str, attr_name: str) -> str | None:
    method = getattr(event, method_name, None)
    if callable(method):
        try:
            value = method()
        except TypeError:
            value = None
        if value not in (None, ""):
            return str(value)

    value = getattr(event, attr_name, None)
    if value not in (None, ""):
        return str(value)
    return None


def _sender_id(event: Any) -> str | None:
    return _event_value(event, "get_sender_id", "sender_id") or _nested_value(
        event, ("message_obj", "sender", "user_id")
    )


def _nested_value(obj: Any, path: tuple[str, ...]) -> str | None:
    current = obj
    for part in path:
        current = getattr(current, part, None)
        if current is None:
            return None
    if current in (None, ""):
        return None
    return str(current)


def _sanitize_tag_value(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_:-]+", "_", value.strip())
    return normalized.strip("_") or "unknown"
