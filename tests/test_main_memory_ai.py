from pathlib import Path
from types import SimpleNamespace
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))


def _ensure_astrbot_stubs():
    astrbot = sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    astrbot.__path__ = getattr(astrbot, "__path__", [])

    api = sys.modules.setdefault("astrbot.api", types.ModuleType("astrbot.api"))
    api.AstrBotConfig = dict
    api.logger = DummyLogger()
    setattr(astrbot, "api", api)

    event = sys.modules.setdefault("astrbot.api.event", types.ModuleType("astrbot.api.event"))
    event.AstrMessageEvent = object
    event.filter = FilterStub()

    provider = sys.modules.setdefault("astrbot.api.provider", types.ModuleType("astrbot.api.provider"))
    provider.LLMResponse = object
    provider.ProviderRequest = object

    star = sys.modules.setdefault("astrbot.api.star", types.ModuleType("astrbot.api.star"))
    star.Context = object
    star.Star = StarStub
    star.StarTools = StarToolsStub

    core = sys.modules.setdefault("astrbot.core", types.ModuleType("astrbot.core"))
    core.__path__ = getattr(core, "__path__", [])
    agent = sys.modules.setdefault("astrbot.core.agent", types.ModuleType("astrbot.core.agent"))
    agent.__path__ = getattr(agent, "__path__", [])
    message = sys.modules.setdefault("astrbot.core.agent.message", types.ModuleType("astrbot.core.agent.message"))
    message.TextPart = TextPartStub


class DummyLogger:
    def warning(self, message):
        pass

    def debug(self, message):
        pass


class CommandGroupStub:
    def __call__(self, func):
        return self

    def command(self, name):
        return lambda func: func


class FilterStub:
    def on_llm_request(self):
        return lambda func: func

    def on_llm_response(self):
        return lambda func: func

    def command_group(self, name):
        return CommandGroupStub()


class StarStub:
    def __init__(self, context):
        self.context = context


class StarToolsStub:
    @staticmethod
    def get_data_dir():
        return ROOT / ".test-data"


class TextPartStub:
    def __init__(self, text=""):
        self.text = text

    def mark_as_temp(self):
        return self


_ensure_astrbot_stubs()
main = importlib.import_module("astrbot_plugin_supermemory.main")


class MainMemoryAiTests(unittest.IsolatedAsyncioTestCase):
    async def test_retention_uses_ai_decision_before_rule_fallback(self):
        plugin = _plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})

        with patch.object(
            main,
            "_call_memory_ai",
            new=AsyncMock(
                return_value=(
                    '{"should_retain": true, "memory_text": "用户正在重构订单同步模块。", '
                    '"scope": "private", "memory_type": "project", "confidence": 0.95, '
                    '"sensitivity": "low", "reason": "project fact"}'
                )
            ),
        ) as call_ai:
            decision = await plugin._retention_decision(_event(), "下午聊了订单同步模块", "好的", "private")

        call_ai.assert_awaited_once()
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.source, "ai")
        self.assertEqual(decision.memory_text, "用户正在重构订单同步模块。")

    async def test_retention_falls_back_to_rules_when_ai_fails(self):
        plugin = _plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})

        with patch.object(main, "_call_memory_ai", new=AsyncMock(side_effect=RuntimeError("boom"))):
            decision = await plugin._retention_decision(_event(), "记住我喜欢简洁回答", "好的", "private")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.source, "rules")
        self.assertEqual(decision.memory_text, "用户喜欢简洁回答")

    async def test_retention_does_not_call_ai_when_memory_ai_is_disabled(self):
        plugin = _plugin({"memory_ai_enabled": False})

        with patch.object(main, "_call_memory_ai", new=AsyncMock()) as call_ai:
            decision = await plugin._retention_decision(_event(), "记住我喜欢简洁回答", "好的", "private")

        call_ai.assert_not_called()
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.source, "rules")

    async def test_recall_queries_use_ai_expansion_and_fallback_to_original(self):
        plugin = _plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})

        with patch.object(
            main,
            "_call_memory_ai",
            new=AsyncMock(return_value='{"queries": ["饮料 偏好", "冰美式"], "reason": "expand"}'),
        ):
            self.assertEqual(await plugin._recall_queries(_event(), "我喜欢什么饮料"), ["我喜欢什么饮料", "饮料 偏好", "冰美式"])

        with patch.object(main, "_call_memory_ai", new=AsyncMock(side_effect=RuntimeError("boom"))):
            self.assertEqual(await plugin._recall_queries(_event(), "我喜欢什么饮料"), ["我喜欢什么饮料"])


def _plugin(config):
    plugin = main.SupermemoryPlugin.__new__(main.SupermemoryPlugin)
    plugin._context = object()
    plugin.config = config
    return plugin


def _event():
    return SimpleNamespace(unified_msg_origin="umo")


if __name__ == "__main__":
    unittest.main()
