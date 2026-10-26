from dataclasses import dataclass
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

astrbot_module = sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
api_module = sys.modules.setdefault("astrbot.api", types.ModuleType("astrbot.api"))
api_module.logger = getattr(api_module, "logger", None)
setattr(astrbot_module, "api", api_module)

from astrbot_plugin_supermemory.commands import run_manual_recall_for_scopes
from astrbot_plugin_supermemory.memory_formatter import GUIDANCE_LINE
from astrbot_plugin_supermemory.supermemory_client import SupermemoryClientError


class CommandsRecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_recall_searches_expanded_queries_with_threshold_and_dedupes_results(self):
        client = FakeSupermemoryClient()

        formatted = await run_manual_recall_for_scopes(
            client,
            query="我喜欢什么饮料",
            scopes=[RecallScope(scope_type="private", container_tag="scope-1")],
            limit=5,
            threshold=0.42,
            search_mode="memories",
            queries=["我喜欢什么饮料", "饮料 偏好", "饮料 偏好"],
        )

        self.assertEqual([call["query"] for call in client.calls], ["我喜欢什么饮料", "饮料 偏好"])
        self.assertTrue(all(call["threshold"] == 0.42 for call in client.calls))
        self.assertEqual(formatted.count("用户喜欢冰美式。"), 1)
        self.assertIn('<supermemory_context scope="private">', formatted)

    async def test_manual_recall_merges_queries_by_rank_within_each_scope(self):
        client = FakeSupermemoryClient(
            {
                ("shared", "q"): [f"群事实{i}" for i in range(5)],
                ("member", "q"): ["用户叫小王", "用户喜欢猫"],
                ("shared", "expanded"): ["扩展群事实", "群事实3"],
                ("member", "expanded"): ["用户喜欢猫"],
            }
        )

        formatted = await run_manual_recall_for_scopes(
            client,
            query="q",
            scopes=[
                RecallScope(scope_type="group_shared", container_tag="shared"),
                RecallScope(scope_type="group_member", container_tag="member"),
            ],
            limit=3,
            threshold=0.6,
            search_mode="memories",
            queries=["expanded"],
        )

        shared_block, member_block = formatted.split("</supermemory_context>\n")
        self.assertEqual(shared_block.splitlines()[1:], ["- 群事实3", "- 群事实0", "- 扩展群事实"])
        self.assertEqual(member_block.splitlines()[1:3], ["- 用户喜欢猫", "- 用户叫小王"])

    async def test_manual_recall_adds_guidance_line_only_when_requested(self):
        client = FakeSupermemoryClient()
        scopes = [RecallScope(scope_type="private", container_tag="scope-1")]

        with_guidance = await run_manual_recall_for_scopes(
            client, query="q", scopes=scopes, limit=5, threshold=0.6, search_mode="memories", guidance=True
        )
        without_guidance = await run_manual_recall_for_scopes(
            client, query="q", scopes=scopes, limit=5, threshold=0.6, search_mode="memories"
        )

        self.assertEqual(with_guidance.splitlines()[0], GUIDANCE_LINE)
        self.assertEqual(with_guidance.count(GUIDANCE_LINE), 1)
        self.assertNotIn(GUIDANCE_LINE, without_guidance)

    async def test_manual_recall_tolerates_partial_failures(self):
        client = FakeSupermemoryClient({("member", "q"): ["用户叫小王"]}, failing_tags={"shared"})

        formatted = await run_manual_recall_for_scopes(
            client,
            query="q",
            scopes=[
                RecallScope(scope_type="group_shared", container_tag="shared"),
                RecallScope(scope_type="group_member", container_tag="member"),
            ],
            limit=5,
            threshold=0.6,
            search_mode="memories",
        )

        self.assertIn("用户叫小王", formatted)
        self.assertNotIn("group_shared", formatted)

    async def test_manual_recall_raises_when_all_requests_fail(self):
        client = FakeSupermemoryClient({}, failing_tags={"shared"})

        with self.assertRaises(SupermemoryClientError):
            await run_manual_recall_for_scopes(
                client,
                query="q",
                scopes=[RecallScope(scope_type="group_shared", container_tag="shared")],
                limit=5,
                threshold=0.6,
                search_mode="memories",
            )


class FakeSupermemoryClient:
    def __init__(self, results=None, failing_tags=()):
        self.calls = []
        self.results = results
        self.failing_tags = set(failing_tags)

    async def search(self, *, query, container_tag, limit, threshold, search_mode):
        self.calls.append(
            {
                "query": query,
                "container_tag": container_tag,
                "limit": limit,
                "threshold": threshold,
                "search_mode": search_mode,
            }
        )
        if container_tag in self.failing_tags:
            raise SupermemoryClientError("boom")
        if self.results is None:
            return {"results": [{"memory": "用户喜欢冰美式。"}]}
        return {"results": [{"memory": text} for text in self.results.get((container_tag, query), [])]}


@dataclass(frozen=True)
class RecallScope:
    scope_type: str
    container_tag: str


if __name__ == "__main__":
    unittest.main()
