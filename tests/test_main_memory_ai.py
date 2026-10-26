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
    def test_retain_metadata_serializes_confidence_as_string(self):
        plugin = _plugin({})
        decision = main.RetainDecision(
            should_retain=True,
            reason="ai_project",
            sensitivity="low",
            keep_user=True,
            keep_assistant=False,
            target_scope_types=("private",),
            memory_type="project",
            confidence=0.95,
            source="ai",
        )

        metadata = plugin._retain_metadata({"scope": "private"}, decision, "new")

        self.assertEqual(metadata["retention_confidence"], "0.95")
        self.assertIsInstance(metadata["retention_confidence"], str)

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
            (decision,) = await plugin._retention_decisions(_event(), "下午聊了订单同步模块", "好的", "private")

        call_ai.assert_awaited_once()
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.source, "ai")
        self.assertEqual(decision.memory_text, "用户正在重构订单同步模块。")

    async def test_retention_falls_back_to_rules_when_ai_fails(self):
        plugin = _plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})

        with patch.object(main, "_call_memory_ai", new=AsyncMock(side_effect=RuntimeError("boom"))):
            (decision,) = await plugin._retention_decisions(_event(), "记住我喜欢简洁回答", "好的", "private")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.source, "rules")
        self.assertEqual(decision.memory_text, "用户喜欢简洁回答")

    async def test_retention_does_not_call_ai_when_memory_ai_is_disabled(self):
        plugin = _plugin({"memory_ai_enabled": False})

        with patch.object(main, "_call_memory_ai", new=AsyncMock()) as call_ai:
            (decision,) = await plugin._retention_decisions(_event(), "记住我喜欢简洁回答", "好的", "private")

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

    async def test_on_llm_response_writes_each_ai_memory_with_timestamp(self):
        plugin = _hook_plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})
        client = plugin._client.return_value
        event = ExtraEvent("我下周五要考雅思，最近在刷听力")
        event.message_obj = SimpleNamespace(timestamp=1792807200)

        with patch.object(
            main,
            "_call_memory_ai",
            new=AsyncMock(
                return_value=(
                    '{"memories": ['
                    '{"text": "用户计划在2026-10-30参加雅思考试。", "memory_type": "event", "confidence": 0.9},'
                    '{"text": "用户最近在练习雅思听力。", "memory_type": "event", "confidence": 0.9}'
                    ']}'
                )
            ),
        ):
            await plugin.on_llm_response(event, SimpleNamespace(completion_text="加油！"))

        calls = client.ingest_conversation.await_args_list
        self.assertEqual(
            [call.kwargs["messages"] for call in calls],
            [
                [{"role": "user", "content": "用户计划在2026-10-30参加雅思考试。"}],
                [{"role": "user", "content": "用户最近在练习雅思听力。"}],
            ],
        )
        self.assertEqual(calls[0].kwargs["metadata"]["retention_timestamp"], "2026-10-24T02:00:00Z")
        self.assertEqual(calls[0].kwargs["metadata"]["retention_type"], "event")

    async def test_request_stores_context_and_response_uses_it_in_ai_prompt(self):
        plugin = _hook_plugin({"memory_ai_enabled": False})
        event = ExtraEvent("那前者呢？")
        req = SimpleNamespace(
            contexts=[
                {"role": "user", "content": "我在考虑用 Redis 还是 Kafka"},
                {"role": "assistant", "content": "各有优劣"},
            ],
            extra_user_content_parts=[],
        )

        with patch.object(
            main,
            "run_manual_recall_for_scopes",
            new=AsyncMock(return_value='<supermemory_context scope="private">x</supermemory_context>'),
        ) as recall:
            await plugin.on_llm_request(event, req)

        self.assertEqual(
            event.get_extra(main.RECENT_CONTEXT_EXTRA_KEY),
            [{"role": "user", "text": "我在考虑用 Redis 还是 Kafka"}, {"role": "assistant", "text": "各有优劣"}],
        )
        self.assertEqual(recall.await_args.kwargs["queries"], ["那前者呢？", "我在考虑用 Redis 还是 Kafka 那前者呢？"])
        self.assertIs(recall.await_args.kwargs["guidance"], True)
        self.assertEqual(len(req.extra_user_content_parts), 1)

        plugin.config.update({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})
        event.message_str = "就用前者吧"
        with patch.object(main, "_call_memory_ai", new=AsyncMock(return_value='{"memories": []}')) as call_ai:
            await plugin.on_llm_response(event, SimpleNamespace(completion_text="好的"))

        prompt = call_ai.await_args.args[2]
        self.assertIn("我在考虑用 Redis 还是 Kafka", prompt)

    async def test_trivial_message_skips_recall_but_still_stores_context(self):
        plugin = _hook_plugin({})
        event = ExtraEvent("谢谢")
        req = SimpleNamespace(contexts=[{"role": "user", "content": "上一句"}], extra_user_content_parts=[])

        with patch.object(main, "run_manual_recall_for_scopes", new=AsyncMock()) as recall:
            await plugin.on_llm_request(event, req)

        recall.assert_not_called()
        self.assertEqual(event.get_extra(main.RECENT_CONTEXT_EXTRA_KEY), [{"role": "user", "text": "上一句"}])
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_context_turns_zero_disables_context(self):
        plugin = _hook_plugin({"context_turns": 0})
        event = ExtraEvent("那前者呢？")
        req = SimpleNamespace(contexts=[{"role": "user", "content": "上一句"}], extra_user_content_parts=[])

        with patch.object(main, "run_manual_recall_for_scopes", new=AsyncMock(return_value="")) as recall:
            await plugin.on_llm_request(event, req)

        self.assertEqual(event.get_extra(main.RECENT_CONTEXT_EXTRA_KEY), [])
        self.assertEqual(recall.await_args.kwargs["queries"], ["那前者呢？"])

    async def test_tool_call_step_does_not_trigger_retention(self):
        plugin = _hook_plugin({"memory_ai_enabled": True, "memory_ai_provider_id": "small"})

        with patch.object(main, "_call_memory_ai", new=AsyncMock()) as call_ai:
            await plugin.on_llm_response(
                ExtraEvent("我喜欢简洁回答"),
                SimpleNamespace(completion_text="", tools_call_name=["web_search"]),
            )

        call_ai.assert_not_called()
        plugin._client.assert_not_called()

    def test_event_datetime_falls_back_to_now_without_message_timestamp(self):
        value = main._event_datetime(SimpleNamespace())

        self.assertIsNotNone(value.tzinfo)


class ExtraEvent:
    def __init__(self, message_str):
        self.unified_msg_origin = "umo"
        self.message_str = message_str
        self.sender_id = "u1"
        self.platform_id = "qq"
        self._extras = {}

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)


def _hook_plugin(config):
    plugin = _plugin({"api_key": "k", "retain_dedupe_enabled": False, **config})
    plugin.store = SimpleNamespace(is_scope_enabled=lambda key: True)
    plugin.salt = "salt"
    plugin._client = AsyncMock(return_value=SimpleNamespace(ingest_conversation=AsyncMock(return_value={})))
    return plugin


def _plugin(config):
    plugin = main.SupermemoryPlugin.__new__(main.SupermemoryPlugin)
    plugin._context = object()
    plugin.config = config
    return plugin


def _event():
    return SimpleNamespace(unified_msg_origin="umo")


if __name__ == "__main__":
    unittest.main()
