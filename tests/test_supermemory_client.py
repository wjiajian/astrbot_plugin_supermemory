import json
import unittest

import httpx

from supermemory_client import SupermemoryClient, SupermemoryClientError


class SupermemoryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_payload_and_headers(self):
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = _client_with_transport(handler)
        self.addAsyncCleanup(client.aclose)

        data = await client.search(
            query="hello",
            container_tag="astrbot_private_x",
            limit=3,
            threshold=0.7,
            search_mode="hybrid",
        )

        self.assertEqual(data["results"], [])
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://api.supermemory.ai/v4/search")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(
            json.loads(request.content.decode()),
            {
                "q": "hello",
                "containerTag": "astrbot_private_x",
                "limit": 3,
                "threshold": 0.7,
                "searchMode": "hybrid",
            },
        )

    async def test_api_base_subpath_is_preserved(self):
        seen: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"results": []})

        client = _client_with_transport(handler, api_base="https://proxy.example.com/supermemory_api")
        self.addAsyncCleanup(client.aclose)

        await client.search(
            query="hello",
            container_tag="astrbot_private_x",
            limit=1,
            threshold=0.6,
            search_mode="memories",
        )

        self.assertEqual(seen[0], "https://proxy.example.com/supermemory_api/v4/search")

    async def test_ingest_conversation_uses_single_container_tag(self):
        payloads: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(request.content.decode())
            return httpx.Response(200, json={})

        client = _client_with_transport(handler, api_base="https://api.supermemory.ai/")
        self.addAsyncCleanup(client.aclose)

        await client.ingest_conversation(
            conversation_id="conv-1",
            messages=[{"role": "user", "content": "hello"}],
            container_tag="astrbot_group_x",
            metadata={"scope": "group"},
        )

        self.assertEqual(
            json.loads(payloads[0]),
            {
                "conversationId": "conv-1",
                "messages": [{"role": "user", "content": "hello"}],
                "containerTags": ["astrbot_group_x"],
                "metadata": {"scope": "group"},
            },
        )

    async def test_http_status_error_is_mapped(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Unauthorized"})

        client = _client_with_transport(handler, api_key="bad-key")
        self.addAsyncCleanup(client.aclose)

        with self.assertRaises(SupermemoryClientError) as context:
            await client.search(
                query="hello",
                container_tag="astrbot_private_x",
                limit=1,
                threshold=0.6,
                search_mode="memories",
            )

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.kind, "http_status")

    async def test_client_reuses_async_client_until_closed(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = _client_with_transport(handler)

        self.assertIsNone(client._client)

        await client.search(
            query="first",
            container_tag="astrbot_private_x",
            limit=1,
            threshold=0.6,
            search_mode="memories",
        )
        shared_client = client._client
        await client.search(
            query="second",
            container_tag="astrbot_private_x",
            limit=1,
            threshold=0.6,
            search_mode="memories",
        )

        self.assertIs(client._client, shared_client)
        self.assertIsNotNone(client._client)
        self.assertFalse(client._client.is_closed)

        await client.aclose()
        self.assertIsNone(client._client)

    async def test_retries_timeout_then_succeeds(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectTimeout("timeout", request=request)
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = _client_with_transport(handler)
        self.addAsyncCleanup(client.aclose)

        await client.search(
            query="hello",
            container_tag="astrbot_private_x",
            limit=1,
            threshold=0.6,
            search_mode="memories",
        )

        self.assertEqual(attempts, 2)

    async def test_retries_500_then_succeeds(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(500, json={"error": "server"})
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = _client_with_transport(handler)
        self.addAsyncCleanup(client.aclose)

        await client.search(
            query="hello",
            container_tag="astrbot_private_x",
            limit=1,
            threshold=0.6,
            search_mode="memories",
        )

        self.assertEqual(attempts, 2)

    async def test_ingest_conversation_does_not_retry_transient_server_errors(self):
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, json={"error": "server"})

        client = _client_with_transport(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaises(SupermemoryClientError):
            await client.ingest_conversation(
                conversation_id="conv-1",
                messages=[{"role": "user", "content": "hello"}],
                container_tag="astrbot_private_x",
            )

        self.assertEqual(attempts, 1)


def _client_with_transport(
    handler,
    api_base: str = "https://api.supermemory.ai",
    api_key: str = "test-key",
) -> SupermemoryClient:
    return SupermemoryClient(
        api_base=api_base,
        api_key=api_key,
        transport=httpx.MockTransport(handler),
        retry_base_delay_seconds=0,
    )


if __name__ == "__main__":
    unittest.main()
