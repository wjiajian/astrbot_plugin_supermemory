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


class FakeSupermemoryClient:
    def __init__(self):
        self.calls = []

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
        return {"results": [{"memory": "用户喜欢冰美式。"}]}


@dataclass(frozen=True)
class RecallScope:
    scope_type: str
    container_tag: str


if __name__ == "__main__":
    unittest.main()
