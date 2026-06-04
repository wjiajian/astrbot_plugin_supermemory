import asyncio
import json
import unittest

import httpx

from supermemory_client import SupermemoryClient, SupermemoryClientError


def run(coro):
    return asyncio.run(coro)


class SupermemoryClientTests(unittest.TestCase):
    def test_search_payload_and_headers(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = SupermemoryClient(
            api_base="https://api.supermemory.ai",
            api_key="test-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )

        data = run(
            client.search(
                query="hello",
                container_tag="astrbot_private_x",
                limit=3,
                threshold=0.7,
                search_mode="hybrid",
            )
        )
        run(client.aclose())

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

    def test_ingest_conversation_uses_single_container_tag(self):
        payloads: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(request.content.decode())
            return httpx.Response(200, json={})

        client = SupermemoryClient(
            api_base="https://api.supermemory.ai/",
            api_key="test-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )

        run(
            client.ingest_conversation(
                conversation_id="conv-1",
                messages=[{"role": "user", "content": "hello"}],
                container_tag="astrbot_group_x",
                metadata={"scope": "group"},
            )
        )
        run(client.aclose())

        self.assertEqual(
            json.loads(payloads[0]),
            {
                "conversationId": "conv-1",
                "messages": [{"role": "user", "content": "hello"}],
                "containerTags": ["astrbot_group_x"],
                "metadata": {"scope": "group"},
            },
        )

    def test_http_status_error_is_mapped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Unauthorized"})

        client = SupermemoryClient(
            api_base="https://api.supermemory.ai",
            api_key="bad-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )

        with self.assertRaises(SupermemoryClientError) as cm:
            run(
                client.search(
                    query="hello",
                    container_tag="astrbot_private_x",
                    limit=1,
                    threshold=0.6,
                    search_mode="memories",
                )
            )
        run(client.aclose())

        self.assertEqual(cm.exception.status_code, 401)
        self.assertEqual(cm.exception.kind, "http_status")

    def test_retries_timeout_then_succeeds(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectTimeout("timeout", request=request)
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = SupermemoryClient(
            api_base="https://api.supermemory.ai",
            api_key="test-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )

        run(
            client.search(
                query="hello",
                container_tag="astrbot_private_x",
                limit=1,
                threshold=0.6,
                search_mode="memories",
            )
        )
        run(client.aclose())

        self.assertEqual(attempts, 2)

    def test_retries_500_then_succeeds(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(500, json={"error": "server"})
            return httpx.Response(200, json={"results": [], "timing": 1, "total": 0})

        client = SupermemoryClient(
            api_base="https://api.supermemory.ai",
            api_key="test-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )

        run(
            client.search(
                query="hello",
                container_tag="astrbot_private_x",
                limit=1,
                threshold=0.6,
                search_mode="memories",
            )
        )
        run(client.aclose())

        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
