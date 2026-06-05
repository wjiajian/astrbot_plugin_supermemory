from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.25
VALID_SEARCH_MODES = {"memories", "hybrid", "documents"}


class SupermemoryClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        kind: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class SupermemoryStatus:
    ok: bool
    message: str


class SupermemoryClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        timeout_seconds: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = httpx.Timeout(float(timeout_seconds))
        self.transport = transport
        self.max_retries = max(0, max_retries)
        self.retry_base_delay_seconds = max(0.0, retry_base_delay_seconds)
        self._client: httpx.AsyncClient | None = None

    async def search(
        self,
        *,
        query: str,
        container_tag: str,
        limit: int,
        threshold: float,
        search_mode: str,
    ) -> dict[str, Any]:
        payload = {
            "q": query,
            "containerTag": container_tag,
            "limit": max(1, int(limit)),
            "threshold": _clamp_threshold(threshold),
            "searchMode": _normalize_search_mode(search_mode),
        }
        return await self._request_json("POST", "v4/search", retryable=True, json=payload)

    async def ingest_conversation(
        self,
        *,
        conversation_id: str,
        messages: list[dict[str, Any]],
        container_tag: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "conversationId": conversation_id,
            "messages": messages,
            "containerTags": [container_tag],
        }
        if metadata:
            payload["metadata"] = metadata
        return await self._request_json("POST", "v4/conversations", retryable=False, json=payload)

    async def check_status(self, container_tag: str) -> SupermemoryStatus:
        try:
            await self.search(
                query="connection check",
                container_tag=container_tag,
                limit=1,
                threshold=0.9,
                search_mode="memories",
            )
        except SupermemoryClientError as exc:
            if exc.kind == "timeout":
                return SupermemoryStatus(False, "连接超时，请检查网络或 request_timeout_seconds。")
            status = exc.status_code
            if status == 401:
                return SupermemoryStatus(False, "认证失败：API Key 无效或未提供。")
            if status == 402:
                return SupermemoryStatus(False, "额度不足：Supermemory 返回 402。")
            if status == 404:
                return SupermemoryStatus(False, "空间不存在或当前 containerTag 不可用。")
            if status is not None:
                return SupermemoryStatus(False, f"Supermemory 返回 HTTP {status}。")
            return SupermemoryStatus(False, f"连接失败：{exc}")
        return SupermemoryStatus(True, "Supermemory 连接正常。")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        retryable: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        attempts = self.max_retries + 1 if retryable else 1
        for attempt in range(attempts):
            try:
                return await self._request_json_once(method, path, **kwargs)
            except SupermemoryClientError as exc:
                if attempt >= attempts - 1 or not _should_retry(exc):
                    raise
                await asyncio.sleep(self.retry_base_delay_seconds * (2**attempt))
        raise SupermemoryClientError("Supermemory request failed after retries")

    async def _request_json_once(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            client = await self._get_client()
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            if not response.content:
                return {}
            try:
                data = response.json()
            except ValueError as exc:
                raise SupermemoryClientError("Supermemory returned invalid JSON") from exc
        except httpx.TimeoutException as exc:
            raise SupermemoryClientError("Supermemory request timed out", kind="timeout") from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise SupermemoryClientError(
                f"Supermemory returned HTTP {status_code}",
                status_code=status_code,
                kind="http_status",
            ) from exc
        except httpx.RequestError as exc:
            raise SupermemoryClientError(f"Supermemory request failed: {exc}", kind="network") from exc

        if not isinstance(data, dict):
            raise SupermemoryClientError("Supermemory returned an unexpected response shape")
        return data

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_base,
                timeout=self.timeout,
                headers=self._headers(),
                transport=self.transport,
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def _normalize_search_mode(search_mode: str) -> str:
    normalized = str(search_mode or "memories").strip().lower()
    if normalized not in VALID_SEARCH_MODES:
        return "memories"
    return normalized


def _clamp_threshold(threshold: float) -> float:
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return 0.6
    return min(1.0, max(0.0, value))


def _should_retry(exc: SupermemoryClientError) -> bool:
    if exc.kind in {"timeout", "network"}:
        return True
    return exc.status_code is not None and exc.status_code >= 500
