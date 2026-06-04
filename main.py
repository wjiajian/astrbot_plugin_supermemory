from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart

from .commands import PluginStateStore, build_help_text, run_manual_recall
from .memory_formatter import format_search_results
from .scope import MemoryScope, build_scope_from_event
from .supermemory_client import SupermemoryClient, SupermemoryClientError


PLUGIN_NAME = "astrbot_plugin_supermemory"


class SupermemoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.store = PluginStateStore(StarTools.get_data_dir())
        self.salt = self.store.get_or_create_salt()
        self.supermemory_client = SupermemoryClient(
            api_base=str(self.config.get("api_base") or "https://api.supermemory.ai"),
            api_key=str(self.config.get("api_key") or ""),
            timeout_seconds=int(self.config.get("request_timeout_seconds") or 8),
        )

    async def terminate(self):
        await self.supermemory_client.aclose()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._base_enabled():
            return
        scope = self._scope(event)
        if not self._scope_type_enabled(scope) or not self.store.is_scope_enabled(scope.scope_key):
            return
        if not self._config_complete():
            return

        query = _event_text(event)
        if not query:
            return

        try:
            formatted = await self._recall_scope(scope, query)
        except SupermemoryClientError as exc:
            _log_warning(f"Supermemory recall failed: {exc}")
            return

        if not formatted:
            return
        if not hasattr(req, "extra_user_content_parts"):
            _log_warning("ProviderRequest does not support extra_user_content_parts; skip Supermemory injection.")
            return

        req.extra_user_content_parts.append(_temporary_text_part(formatted))

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self._base_enabled() or not _bool_config(self.config, "retain_enabled", True):
            return
        scope = self._scope(event)
        if not self._scope_type_enabled(scope) or not self.store.is_scope_enabled(scope.scope_key):
            return
        if not self._config_complete():
            return

        messages = self._retain_messages(event, resp)
        if not messages:
            return

        try:
            await self._retain_scope(scope, messages)
        except SupermemoryClientError as exc:
            _log_warning(f"Supermemory retain failed: {exc}")

    @filter.command_group("supermemory")
    def supermemory(self, event: AstrMessageEvent):
        pass

    @supermemory.command("status")
    async def supermemory_status(self, event: AstrMessageEvent):
        """检查 Supermemory 配置和连接状态。"""
        scope = self._scope(event)
        lines = [
            "Supermemory 状态",
            f"全局启用：{'是' if self._base_enabled() else '否'}",
            f"当前 scope：{scope.scope_type}",
            f"当前个人 containerTag：{scope.container_tag}",
            f"当前会话：{'开启' if self.store.is_scope_enabled(scope.scope_key) else '关闭'}",
            f"配置完整：{'是' if self._config_complete() else '否'}",
        ]
        if scope.group_container_tag:
            lines.append(f"当前群公共 containerTag：{scope.group_container_tag}")
        if self._config_complete():
            status = await self._client().check_status(scope.container_tag)
            lines.append(f"连接检查：{status.message}")
        else:
            lines.append("连接检查：跳过，请先填写 api_key。")
        yield event.plain_result("\n".join(lines))

    @supermemory.command("recall")
    async def supermemory_recall(self, event: AstrMessageEvent, query: str):
        """手动检索当前会话 scope 下的 Supermemory 记忆。"""
        if not self._base_enabled():
            yield event.plain_result("Supermemory 当前未启用。")
            return
        if not self._config_complete():
            yield event.plain_result("配置不完整，请先填写 api_key。")
            return

        scope = self._scope(event)
        if not self._scope_type_enabled(scope):
            yield event.plain_result(f"当前 {scope.scope_type} scope 的记忆未启用。")
            return
        if not self.store.is_scope_enabled(scope.scope_key):
            yield event.plain_result("当前会话记忆已关闭。")
            return

        try:
            result = await self._manual_recall_scope(
                self._client(),
                scope=scope,
                query=query,
            )
        except SupermemoryClientError as exc:
            _log_warning(f"Supermemory manual recall failed: {exc}")
            result = f"手动检索失败：{exc}"
        yield event.plain_result(result)

    @supermemory.command("on")
    async def supermemory_on(self, event: AstrMessageEvent):
        """启用当前会话的 Supermemory 记忆。"""
        scope = self._scope(event)
        self.store.set_scope_enabled(scope.scope_key, True)
        yield event.plain_result("已启用当前会话 Supermemory 记忆。")

    @supermemory.command("off")
    async def supermemory_off(self, event: AstrMessageEvent):
        """关闭当前会话的 Supermemory 记忆。"""
        scope = self._scope(event)
        self.store.set_scope_enabled(scope.scope_key, False)
        yield event.plain_result("已关闭当前会话 Supermemory 记忆。")

    @supermemory.command("help")
    async def supermemory_help(self, event: AstrMessageEvent):
        """显示 Supermemory 命令帮助。"""
        scope = self._scope(event)
        yield event.plain_result(build_help_text(self.store.is_scope_enabled(scope.scope_key)))

    def _client(self) -> SupermemoryClient:
        return self.supermemory_client

    async def _recall_scope(self, scope: MemoryScope, query: str) -> str:
        formatted_parts: list[str] = []
        if scope.group_container_tag and self._group_shared_memory_enabled():
            raw_group = await self._client().search(
                query=query,
                container_tag=scope.group_container_tag,
                limit=self._recall_limit(),
                threshold=self._search_threshold(),
                search_mode=self._search_mode(),
            )
            formatted_group = format_search_results(raw_group, limit=self._recall_limit(), title="group_shared")
            if formatted_group:
                formatted_parts.append(formatted_group)

        raw_personal = await self._client().search(
            query=query,
            container_tag=scope.container_tag,
            limit=self._recall_limit(),
            threshold=self._search_threshold(),
            search_mode=self._search_mode(),
        )
        formatted_personal = format_search_results(raw_personal, limit=self._recall_limit(), title=_scope_title(scope))
        if formatted_personal:
            formatted_parts.append(formatted_personal)
        return "\n".join(formatted_parts)

    async def _retain_scope(self, scope: MemoryScope, messages: list[dict[str, str]]) -> None:
        await self._client().ingest_conversation(
            conversation_id=_conversation_id(scope.container_tag, "personal"),
            messages=messages,
            container_tag=scope.container_tag,
            metadata=scope.metadata,
        )
        if scope.group_container_tag and scope.group_metadata and self._group_shared_memory_enabled():
            await self._client().ingest_conversation(
                conversation_id=_conversation_id(scope.group_container_tag, "group_shared"),
                messages=messages,
                container_tag=scope.group_container_tag,
                metadata=scope.group_metadata,
            )

    async def _manual_recall_scope(
        self,
        client: SupermemoryClient,
        *,
        scope: MemoryScope,
        query: str,
    ) -> str:
        parts: list[str] = []
        if scope.group_container_tag and self._group_shared_memory_enabled():
            parts.append(
                await run_manual_recall(
                    client,
                    query=query,
                    container_tag=scope.group_container_tag,
                    limit=self._recall_limit(),
                    threshold=self._search_threshold(),
                    search_mode=self._search_mode(),
                    title="group_shared",
                )
            )
        parts.append(
            await run_manual_recall(
                client,
                query=query,
                container_tag=scope.container_tag,
                limit=self._recall_limit(),
                threshold=self._search_threshold(),
                search_mode=self._search_mode(),
                title=_scope_title(scope),
            )
        )
        found = [part for part in parts if not part.startswith("当前会话 scope 下没有召回到相关记忆。")]
        if found:
            return "\n".join(found)
        return "当前会话 scope 下没有召回到相关记忆。"

    def _scope(self, event: AstrMessageEvent) -> MemoryScope:
        scope = build_scope_from_event(event, self.salt)
        if scope.scope_type == "private" and _event_looks_like_group(event):
            _log_debug("Supermemory scope fallback: group-like event has no group_id; using private scope.")
        return scope

    def _base_enabled(self) -> bool:
        return _bool_config(self.config, "enabled", True)

    def _config_complete(self) -> bool:
        return bool(str(self.config.get("api_key") or "").strip())

    def _recall_limit(self) -> int:
        try:
            return max(1, int(self.config.get("recall_limit") or 5))
        except (TypeError, ValueError):
            return 5

    def _search_threshold(self) -> float:
        try:
            return min(1.0, max(0.0, float(self.config.get("search_threshold", 0.6))))
        except (TypeError, ValueError):
            return 0.6

    def _search_mode(self) -> str:
        value = str(self.config.get("search_mode") or "memories").strip().lower()
        if value not in {"memories", "hybrid", "documents"}:
            return "memories"
        return value

    def _scope_type_enabled(self, scope: MemoryScope) -> bool:
        if scope.scope_type == "group":
            return _bool_config(self.config, "enable_group_memory", True)
        return _bool_config(self.config, "enable_private_memory", True)

    def _group_shared_memory_enabled(self) -> bool:
        return _bool_config(self.config, "enable_group_shared_memory", True)

    def _retain_messages(self, event: AstrMessageEvent, resp: LLMResponse) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if _bool_config(self.config, "retain_user_message", True):
            user_text = _event_text(event)
            if user_text:
                messages.append({"role": "user", "content": user_text})
        if _bool_config(self.config, "retain_assistant_message", True):
            assistant_text = _response_text(resp)
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
        return messages


def _event_text(event: Any) -> str:
    return str(getattr(event, "message_str", "") or "").strip()


def _response_text(resp: Any) -> str:
    return str(getattr(resp, "completion_text", "") or "").strip()


def _event_looks_like_group(event: Any) -> bool:
    method = getattr(event, "get_message_type", None)
    if callable(method):
        try:
            return str(method()).lower() == "group"
        except (AttributeError, TypeError, ValueError):
            return False
    message_obj = getattr(event, "message_obj", None)
    message_type = getattr(message_obj, "type", None) or getattr(message_obj, "message_type", None)
    return str(message_type).lower() == "group"


def _temporary_text_part(text: str) -> Any:
    try:
        part = TextPart(text=text)
    except TypeError:
        part = TextPart(text)
    return part.mark_as_temp()


def _conversation_id(container_tag: str, layer: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{container_tag}_{layer}_{timestamp}_{uuid.uuid4().hex[:8]}"


def _scope_title(scope: MemoryScope) -> str:
    if scope.scope_type == "group":
        return "group_member"
    return "private"


def _bool_config(config: Any, key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _log_warning(message: str) -> None:
    if logger is not None:
        logger.warning(message)


def _log_debug(message: str) -> None:
    if logger is not None:
        logger.debug(message)
