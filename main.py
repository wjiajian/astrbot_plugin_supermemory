from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart

from .commands import PluginStateStore, build_help_text, run_manual_recall_for_scopes
from .memory_formatter import format_recall_results
from .scope import MemoryScope, MemoryScopes, build_scopes_from_event
from .supermemory_client import SupermemoryClient, SupermemoryClientError


PLUGIN_NAME = "astrbot_plugin_supermemory"


class SupermemoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.store = PluginStateStore(StarTools.get_data_dir())
        self.salt = self.store.get_or_create_salt()
        self.supermemory_client: SupermemoryClient | None = None
        self.supermemory_client_signature: tuple[str, str, int] | None = None

    async def terminate(self):
        if self.supermemory_client is not None:
            await self.supermemory_client.aclose()

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._base_enabled():
            return
        scopes = self._scopes(event)
        if not self._scope_type_enabled(scopes.primary) or not self.store.is_scope_enabled(scopes.primary.scope_key):
            return
        if not self._config_complete():
            return

        query = _event_text(event)
        if not query:
            return

        client = await self._client()
        try:
            limit = self._recall_limit()
            threshold = self._search_threshold()
            search_mode = self._search_mode()
            formatted_parts: list[str] = []
            for recall_scope in self._active_memory_scopes(scopes.recall_scopes):
                raw = await client.search(
                    query=query,
                    container_tag=recall_scope.container_tag,
                    limit=limit,
                    threshold=threshold,
                    search_mode=search_mode,
                )
                formatted = format_recall_results(raw, limit=limit, title=recall_scope.scope_type)
                if formatted:
                    formatted_parts.append(formatted)
            formatted = "\n".join(formatted_parts)
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
        scopes = self._scopes(event)
        if not self._scope_type_enabled(scopes.primary) or not self.store.is_scope_enabled(scopes.primary.scope_key):
            return
        if not self._config_complete():
            return

        messages = self._retain_messages(event, resp)
        if not messages:
            return

        try:
            client = await self._client()
            for retain_scope in self._active_memory_scopes(scopes.retain_scopes):
                await client.ingest_conversation(
                    conversation_id=_conversation_id(retain_scope.container_tag, retain_scope.scope_type),
                    messages=messages,
                    container_tag=retain_scope.container_tag,
                    metadata=retain_scope.metadata,
                )
        except SupermemoryClientError as exc:
            _log_warning(f"Supermemory retain failed: {exc}")

    @filter.command_group("supermemory")
    def supermemory(self, event: AstrMessageEvent):
        pass

    @supermemory.command("status")
    async def supermemory_status(self, event: AstrMessageEvent):
        """检查 Supermemory 配置和连接状态。"""
        scopes = self._scopes(event)
        scope = scopes.primary
        lines = [
            "Supermemory 状态",
            f"全局启用：{'是' if self._base_enabled() else '否'}",
            f"当前 scope：{scope.scope_type}",
            f"当前 containerTag：{scope.container_tag}",
            f"当前会话：{'开启' if self.store.is_scope_enabled(scope.scope_key) else '关闭'}",
            f"配置完整：{'是' if self._config_complete() else '否'}",
        ]
        group_shared_scope = _find_scope(scopes.recall_scopes, "group_shared")
        if group_shared_scope is not None and self._group_shared_memory_enabled():
            lines.append(f"当前群公共 containerTag：{group_shared_scope.container_tag}")
        if self._config_complete():
            status = await (await self._client()).check_status(scope.container_tag)
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

        scopes = self._scopes(event)
        scope = scopes.primary
        if not self._scope_type_enabled(scope):
            yield event.plain_result(f"当前 {scope.scope_type} scope 的记忆未启用。")
            return
        if not self.store.is_scope_enabled(scope.scope_key):
            yield event.plain_result("当前会话记忆已关闭。")
            return

        try:
            result = await run_manual_recall_for_scopes(
                await self._client(),
                query=query,
                scopes=self._active_memory_scopes(scopes.recall_scopes),
                limit=self._recall_limit(),
                threshold=self._search_threshold(),
                search_mode=self._search_mode(),
            )
        except SupermemoryClientError as exc:
            _log_warning(f"Supermemory manual recall failed: {exc}")
            result = f"手动检索失败：{exc}"
        yield event.plain_result(result)

    @supermemory.command("on")
    async def supermemory_on(self, event: AstrMessageEvent):
        """启用当前会话的 Supermemory 记忆。"""
        scope = self._scopes(event).primary
        self.store.set_scope_enabled(scope.scope_key, True)
        yield event.plain_result("已启用当前会话 Supermemory 记忆。")

    @supermemory.command("off")
    async def supermemory_off(self, event: AstrMessageEvent):
        """关闭当前会话的 Supermemory 记忆。"""
        scope = self._scopes(event).primary
        self.store.set_scope_enabled(scope.scope_key, False)
        yield event.plain_result("已关闭当前会话 Supermemory 记忆。")

    @supermemory.command("help")
    async def supermemory_help(self, event: AstrMessageEvent):
        """显示 Supermemory 命令帮助。"""
        scope = self._scopes(event).primary
        yield event.plain_result(build_help_text(self.store.is_scope_enabled(scope.scope_key)))

    async def _client(self) -> SupermemoryClient:
        signature = self._client_signature()
        if self.supermemory_client is not None and self.supermemory_client_signature == signature:
            return self.supermemory_client

        if self.supermemory_client is not None:
            await self.supermemory_client.aclose()

        api_base, api_key, timeout_seconds = signature
        self.supermemory_client = SupermemoryClient(
            api_base=api_base,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        self.supermemory_client_signature = signature
        return self.supermemory_client

    def _client_signature(self) -> tuple[str, str, int]:
        return (
            str(self.config.get("api_base") or "https://api.supermemory.ai"),
            str(self.config.get("api_key") or ""),
            self._request_timeout_seconds(),
        )

    def _scopes(self, event: AstrMessageEvent) -> MemoryScopes:
        scopes = build_scopes_from_event(event, self.salt)
        if scopes.primary.scope_type == "private" and _event_looks_like_group(event):
            _log_debug("Supermemory scope fallback: group-like event has no group_id; using private scope.")
        return scopes

    def _base_enabled(self) -> bool:
        return _bool_config(self.config, "enabled", True)

    def _config_complete(self) -> bool:
        return bool(str(self.config.get("api_key") or "").strip())

    def _recall_limit(self) -> int:
        try:
            return max(1, int(self.config.get("recall_limit") or 5))
        except (TypeError, ValueError):
            return 5

    def _request_timeout_seconds(self) -> int:
        try:
            return max(1, int(self.config.get("request_timeout_seconds") or 8))
        except (TypeError, ValueError):
            return 8

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
        if scope.scope_type in {"group_shared", "group_member"}:
            return _bool_config(self.config, "enable_group_memory", True)
        return _bool_config(self.config, "enable_private_memory", True)

    def _group_shared_memory_enabled(self) -> bool:
        return _bool_config(self.config, "enable_group_shared_memory", True)

    def _memory_scope_enabled(self, scope: MemoryScope) -> bool:
        if not self._scope_type_enabled(scope):
            return False
        if scope.scope_type == "group_shared":
            return self._group_shared_memory_enabled()
        return True

    def _active_memory_scopes(self, scopes: list[MemoryScope]) -> list[MemoryScope]:
        return [scope for scope in scopes if self._memory_scope_enabled(scope)]

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


def _find_scope(scopes: list[MemoryScope], scope_type: str) -> MemoryScope | None:
    for scope in scopes:
        if scope.scope_type == scope_type:
            return scope
    return None


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
