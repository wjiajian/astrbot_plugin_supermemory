from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import secrets
import uuid
from typing import Any, TYPE_CHECKING, Protocol, Sequence

from astrbot.api import logger

from .memory_formatter import DEFAULT_ITEM_MAX_CHARS, MAX_EXTRACT_DEPTH, dedupe_memories, extract_memories, format_recall_results

if TYPE_CHECKING:
    from .supermemory_client import SupermemoryClient


SALT_FILE = "salt.txt"
STATE_FILE = "scope_state.json"


@dataclass
class ScopeSwitchState:
    disabled_scopes: set[str]


class RecallScope(Protocol):
    scope_type: str
    container_tag: str


class PluginStateStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.salt_path = self.data_dir / SALT_FILE
        self.state_path = self.data_dir / STATE_FILE

    def get_or_create_salt(self) -> str:
        if self.salt_path.exists():
            salt = self.salt_path.read_text(encoding="utf-8").strip()
            if salt:
                return salt

        salt = secrets.token_hex(32)
        self.salt_path.write_text(salt, encoding="utf-8")
        return salt

    def is_scope_enabled(self, scope_key: str) -> bool:
        return scope_key not in self._load_state().disabled_scopes

    def set_scope_enabled(self, scope_key: str, enabled: bool) -> None:
        state = self._load_state()
        if enabled:
            state.disabled_scopes.discard(scope_key)
        else:
            state.disabled_scopes.add(scope_key)
        self._save_state(state)

    def _load_state(self) -> ScopeSwitchState:
        if not self.state_path.exists():
            return ScopeSwitchState(disabled_scopes=set())
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(f"Supermemory scope state file is invalid JSON: {self.state_path}: {exc}")
            return ScopeSwitchState(disabled_scopes=set())
        except OSError as exc:
            logger.warning(f"Supermemory scope state file could not be read: {self.state_path}: {exc}")
            return ScopeSwitchState(disabled_scopes=set())
        disabled = raw.get("disabled_scopes", [])
        if not isinstance(disabled, list):
            disabled = []
        return ScopeSwitchState(disabled_scopes={str(item) for item in disabled})

    def _save_state(self, state: ScopeSwitchState) -> None:
        payload = {"disabled_scopes": sorted(state.disabled_scopes)}
        tmp_path = self.state_path.with_name(f"{self.state_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.state_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def build_help_text(scope_enabled: bool | None = None) -> str:
    lines = [
        "Supermemory 命令：",
        "/supermemory status - 检查配置和 Supermemory 连通性",
        "/supermemory recall <query> - 在当前会话 scope 下手动检索记忆",
        "/supermemory on - 启用当前会话记忆",
        "/supermemory off - 关闭当前会话记忆",
        "/supermemory help - 显示帮助",
    ]
    if scope_enabled is not None:
        lines.append(f"当前会话记忆：{'开启' if scope_enabled else '关闭'}")
    return "\n".join(lines)


async def run_manual_recall(
    client: SupermemoryClient,
    *,
    query: str,
    container_tag: str,
    limit: int,
    threshold: float,
    search_mode: str,
    title: str = "memory",
    item_max_chars: int = DEFAULT_ITEM_MAX_CHARS,
    max_extract_depth: int = MAX_EXTRACT_DEPTH,
    queries: Sequence[str] | None = None,
) -> str:
    return await run_manual_recall_for_scopes(
        client,
        query=query,
        scopes=[
            _ManualMemoryScope(
                scope_type=title,
                container_tag=container_tag,
            )
        ],
        limit=limit,
        threshold=threshold,
        search_mode=search_mode,
        item_max_chars=item_max_chars,
        max_extract_depth=max_extract_depth,
        queries=queries,
    )


async def run_manual_recall_for_scopes(
    client: SupermemoryClient,
    *,
    query: str,
    scopes: Sequence[RecallScope],
    limit: int,
    threshold: float,
    search_mode: str,
    item_max_chars: int = DEFAULT_ITEM_MAX_CHARS,
    max_extract_depth: int = MAX_EXTRACT_DEPTH,
    queries: Sequence[str] | None = None,
    empty_message: str = "当前会话 scope 下没有召回到相关记忆。",
) -> str:
    formatted_parts: list[str] = []
    recall_queries = _recall_queries(query, queries)
    for scope in scopes:
        memories: list[Any] = []
        for recall_query in recall_queries:
            raw = await client.search(
                query=recall_query,
                container_tag=scope.container_tag,
                limit=limit,
                threshold=threshold,
                search_mode=search_mode,
            )
            memories.extend(extract_memories(raw))
        memories = dedupe_memories(memories, max_extract_depth=max_extract_depth)
        formatted = format_recall_results(
            memories,
            limit=limit,
            item_max_chars=item_max_chars,
            title=scope.scope_type,
            max_extract_depth=max_extract_depth,
        )
        if formatted:
            formatted_parts.append(formatted)

    if not formatted_parts:
        return empty_message
    return "\n".join(formatted_parts)


def _recall_queries(query: str, queries: Sequence[str] | None) -> list[str]:
    values = list(queries or [])
    if query not in values:
        values.insert(0, query)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


@dataclass(frozen=True)
class _ManualMemoryScope:
    scope_type: str
    container_tag: str
