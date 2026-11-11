from pathlib import Path
import asyncio
import json
import sys
import tempfile
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot_plugin_supermemory.companion_bridge import (  # noqa: E402
    BEGIN_RESERVED,
    BEGIN_SKIP,
    BEGIN_UNAVAILABLE,
    OUTCOME_DEFINITE,
    OUTCOME_UNKNOWN,
    PendingRecordStore,
    SCHEMA,
    CompanionMemoryBridge,
    WrittenMemoryIdStore,
    build_record_context,
    build_record_metadata,
    build_record_container_tag,
    build_record_conversation_id,
    classify_write_failure,
    find_companion_api,
)
from astrbot_plugin_supermemory.supermemory_client import SupermemoryClient, SupermemoryClientError  # noqa: E402
from astrbot_plugin_supermemory.scope import hash_identifier  # noqa: E402


TARGET = "https://api.supermemory.ai|" + hash_identifier("salt-123", "companion_account", "key-1")
OTHER_TARGET = "https://api.supermemory.ai|" + hash_identifier("salt-123", "companion_account", "key-2")


class FakeRegistration:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id


class FakeExtensionApi:
    memory_provider_api_version = SCHEMA

    def __init__(self, *, fail_unregister: bool = False) -> None:
        self.calls: list[dict] = []
        self.unregistered: list[FakeRegistration] = []
        self.fail_unregister = fail_unregister

    def register_memory_provider(self, provider_id, **kwargs):
        self.calls.append({"provider_id": provider_id, **kwargs})
        return FakeRegistration(provider_id)

    def unregister_memory_provider(self, registration):
        if self.fail_unregister:
            raise RuntimeError("registry is gone")
        self.unregistered.append(registration)
        return True


class FakeCompanionHost:
    """陪伴插件实例的最小等价物：它把 extension_api 挂在自己身上。"""

    def __init__(self, api) -> None:
        self.extension_api = api


class FakeStar:
    def __init__(self, star_cls, name: str = "astrbot_plugin_private_companion",
                 activated: bool = True) -> None:
        self.name = name
        self.root_dir_name = name
        self.activated = activated
        self.star_cls = star_cls


class FakeContext:
    def __init__(self, stars) -> None:
        self._stars = list(stars)

    def get_all_stars(self):
        return self._stars


class FakeConversationClient:
    def __init__(self) -> None:
        self.retained: list[dict] = []
        self.fail = False
        self.error: Exception | None = None
        self.hang_on: set[str] = set()

    async def ingest_conversation(self, **kwargs):
        if self.fail:
            if self.error is not None:
                raise self.error
            raise RuntimeError("supermemory down")
        if kwargs.get("metadata", {}).get("memory_id") in self.hang_on:
            await asyncio.sleep(30)
        self.retained.append(kwargs)
        return {"status": "accepted"}

    def retained_ids(self) -> list[str]:
        return [call["metadata"]["memory_id"] for call in self.retained]


class FakePlugin:
    def __init__(self, context, *, enabled: bool = True, data_dir: Path | None = None,
                 config: dict | None = None, api_key: str = "key-1") -> None:
        self._context = context
        self.salt = "salt-123"
        self._enabled = enabled
        self.client = FakeConversationClient()
        self.store = type("Store", (), {"data_dir": data_dir})()
        # 陪伴接入开关默认关闭，测试里要显式打开
        self.config = {"companion_provider_enabled": True, "request_timeout_seconds": 8, "api_key": api_key}
        self.config.update(config or {})

    def _base_enabled(self) -> bool:
        return self._enabled

    def _config_complete(self) -> bool:
        return bool(str(self.config.get("api_key") or "").strip())

    async def _client(self):
        return self.client

def record(**overrides):
    base = {
        "schema": SCHEMA,
        "source_plugin": "astrbot_plugin_private_companion",
        "memory_id": "mem-1",
        "kind": "persona_life",
        "content": "今天写日记：午后在窗边看雨。",
        "platform": "aiocqhttp",
        "occurred_at": "2026-10-31T09:00:00+08:00",
        # 陪伴插件把 native_scope_id（私聊里是 sender ID）拼进 session_id
        "session_id": "private_companion_v2:persona_life:native-1",
        "context": {
            "scope": {"installation_id": "inst-1", "platform": "aiocqhttp",
                      "kind": "private", "native_scope_id": "native-1",
                      "conversation_id": "conv-1", "bot_id": "bot-1"},
            "role": "role-7",
            "user": {"platform": "aiocqhttp", "subject_id": "sender-777"},
            "principal": {"host_id": "host-9", "login": "sender-777"},
        },
    }
    base.update(overrides)
    return base


def companion_context(api=None):
    api = api or FakeExtensionApi()
    return FakeContext([FakeStar(FakeCompanionHost(api)), FakeStar(object(), name="other")]), api


class FindCompanionApiTests(unittest.TestCase):
    def test_finds_activated_companion_star(self):
        context, api = companion_context()
        self.assertIs(find_companion_api(context), api)

    def test_skips_inactive_star(self):
        api = FakeExtensionApi()
        context = FakeContext([FakeStar(FakeCompanionHost(api), activated=False)])
        self.assertIsNone(find_companion_api(context))

    def test_skips_mismatched_api_version(self):
        api = FakeExtensionApi()
        api.memory_provider_api_version = "companion.external-memory.v0"
        context = FakeContext([FakeStar(FakeCompanionHost(api))])
        self.assertIsNone(find_companion_api(context))

    def test_reports_rejection_reason(self):
        api = FakeExtensionApi()
        api.memory_provider_api_version = "v0"
        reasons: list[str] = []
        find_companion_api(FakeContext([FakeStar(FakeCompanionHost(api))]), on_reject=reasons.append)
        self.assertTrue(reasons and "version" in reasons[0])

    def test_returns_none_without_registry(self):
        self.assertIsNone(find_companion_api(object()))


class RecordMappingTests(unittest.TestCase):
    def test_container_is_persona_scoped_and_hashed(self):
        tag = build_record_container_tag("salt-123", record())
        self.assertTrue(tag.startswith("astrbot_persona_aiocqhttp_"))
        self.assertNotIn("role-7", tag)
        self.assertNotIn("inst-1", tag)
        self.assertLessEqual(len(tag), 100)
        self.assertRegex(tag, r"^[A-Za-z0-9_-]+$")

    def test_container_separates_roles_and_installations(self):
        original = record()
        original_tag = build_record_container_tag("salt-123", original)
        for field, replacement in (("role", "role-8"), ("installation_id", "inst-2")):
            other = record()
            if field == "role":
                other["context"][field] = replacement
            else:
                other["context"]["scope"][field] = replacement
            self.assertNotEqual(original_tag, build_record_container_tag("salt-123", other))
        self.assertNotEqual(original_tag, build_record_container_tag("salt-456", original))

    def test_role_identifiers_are_hashed_without_lossy_sanitization(self):
        tags = []
        for role in ("role/a", "role?a", "r" * 100 + "a", "r" * 100 + "b"):
            payload = record()
            payload["context"]["role"] = role
            tags.append(build_record_container_tag("salt-123", payload))
        self.assertEqual(len(set(tags)), 4)

    def test_conversation_id_is_stable_and_scoped_to_record(self):
        first = build_record_conversation_id("salt-123", record())
        self.assertEqual(first, build_record_conversation_id("salt-123", record()))
        self.assertNotEqual(first, build_record_conversation_id("salt-123", record(memory_id="mem-2")))
        self.assertNotIn("mem-1", first)

    def test_metadata_carries_identity_fields(self):
        metadata = build_record_metadata(record(), "salt-123")
        self.assertEqual(metadata["memory_id"], "mem-1")
        self.assertEqual(metadata["kind"], "persona_life")
        self.assertNotIn("session_id", metadata)

    def test_session_id_is_hashed_not_uploaded(self):
        metadata = build_record_metadata(record(), "salt-123")
        self.assertIn("session_id_hash", metadata)
        self.assertNotIn("native-1", json.dumps(metadata, ensure_ascii=False))
        # 同一个 session 稳定，换 salt 就换值
        other = build_record_metadata(record(), "salt-999")
        self.assertNotEqual(metadata["session_id_hash"], other["session_id_hash"])

    def test_context_label_is_human_readable(self):
        self.assertIn("diary", build_record_context(record()))
        self.assertIn("proactive", build_record_context(record(kind="proactive_message")))


class FailureClassificationTests(unittest.TestCase):
    def test_timeout_is_unknown(self):
        self.assertEqual(
            classify_write_failure(SupermemoryClientError("timed out", kind="timeout")),
            OUTCOME_UNKNOWN,
        )

    def test_http_4xx_is_definite(self):
        self.assertEqual(
            classify_write_failure(SupermemoryClientError("bad request", status_code=400, kind="http_status")),
            OUTCOME_DEFINITE,
        )

    def test_http_5xx_is_unknown(self):
        self.assertEqual(
            classify_write_failure(SupermemoryClientError("server error", status_code=503, kind="http_status")),
            OUTCOME_UNKNOWN,
        )

    def test_connect_error_never_reached_the_server(self):
        try:
            try:
                raise httpx.ConnectError("no route to host")
            except httpx.ConnectError as cause:
                raise SupermemoryClientError("Supermemory request failed", kind="network") from cause
        except SupermemoryClientError as exc:
            self.assertEqual(classify_write_failure(exc), OUTCOME_DEFINITE)

    def test_read_error_outcome_is_unknown(self):
        try:
            try:
                raise httpx.ReadError("connection reset")
            except httpx.ReadError as cause:
                raise SupermemoryClientError("Supermemory request failed", kind="network") from cause
        except SupermemoryClientError as exc:
            self.assertEqual(classify_write_failure(exc), OUTCOME_UNKNOWN)

    def test_plain_error_is_definite(self):
        self.assertEqual(classify_write_failure(RuntimeError("bad record")), OUTCOME_DEFINITE)


class BridgeRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_declares_model_memory_and_callbacks(self):
        context, api = companion_context()
        plugin = FakePlugin(context)
        bridge = CompanionMemoryBridge(plugin)

        self.assertTrue(await bridge.ensure_registered())
        call = api.calls[0]
        self.assertEqual(call["provider_id"], "astrbot_plugin_supermemory")
        self.assertTrue(call["handles_model_memory"])
        self.assertEqual(call["priority"], 0)
        self.assertNotIn("read", call)
        self.assertNotIn("summary", call)
        # 8 秒请求超时 + 2 秒余量
        self.assertEqual(call["timeout_seconds"], 10.0)

    async def test_callback_timeout_follows_request_timeout(self):
        context, api = companion_context()
        plugin = FakePlugin(context, config={"request_timeout_seconds": 30})
        await CompanionMemoryBridge(plugin).ensure_registered()
        self.assertEqual(api.calls[0]["timeout_seconds"], 32.0)

    async def test_disabled_by_default(self):
        context, api = companion_context()
        plugin = FakePlugin(context)
        plugin.config = {"request_timeout_seconds": 8}
        bridge = CompanionMemoryBridge(plugin)

        self.assertFalse(bridge.config_enabled())
        self.assertFalse(await bridge.ensure_registered())
        self.assertEqual(api.calls, [])

    async def test_string_false_is_not_enabled(self):
        context, api = companion_context()
        bridge = CompanionMemoryBridge(FakePlugin(context, config={"companion_provider_enabled": "false"}))
        self.assertFalse(bridge.config_enabled())
        self.assertFalse(await bridge.ensure_registered())
        self.assertEqual(api.calls, [])

    async def test_register_is_idempotent(self):
        context, api = companion_context()
        bridge = CompanionMemoryBridge(FakePlugin(context))
        await bridge.ensure_registered()
        await bridge.ensure_registered()
        self.assertEqual(len(api.calls), 1)

    async def test_register_without_companion_is_noop(self):
        bridge = CompanionMemoryBridge(FakePlugin(FakeContext([])))
        self.assertFalse(await bridge.ensure_registered())

    async def test_reregisters_after_companion_reload(self):
        context, first_api = companion_context()
        plugin = FakePlugin(context)
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()

        # 陪伴插件重载：换了一个新的 extension_api
        second_api = FakeExtensionApi()
        context._stars = [FakeStar(FakeCompanionHost(second_api))]
        self.assertTrue(await bridge.ensure_registered())
        self.assertEqual(len(first_api.unregistered), 1)
        self.assertEqual(len(second_api.calls), 1)

    async def test_unregisters_when_plugin_switch_turns_off(self):
        context, api = companion_context()
        plugin = FakePlugin(context)
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()

        plugin._enabled = False
        self.assertFalse(await bridge.ensure_registered())
        self.assertEqual(len(api.unregistered), 1)

    async def test_disabling_config_unregisters(self):
        context, api = companion_context()
        plugin = FakePlugin(context)
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()

        plugin.config["companion_provider_enabled"] = False
        self.assertFalse(await bridge.ensure_registered())
        self.assertEqual(len(api.unregistered), 1)

    async def test_failed_unregister_keeps_registration_for_retry(self):
        api = FakeExtensionApi(fail_unregister=True)
        context, _ = companion_context(api)
        bridge = CompanionMemoryBridge(FakePlugin(context))
        await bridge.ensure_registered()

        await bridge.disconnect()
        self.assertTrue(bridge.registered)
        self.assertFalse(await bridge.ensure_registered())
        self.assertEqual(len(api.calls), 1)
        self.assertFalse(bridge.is_active())
        api.fail_unregister = False
        await bridge.disconnect()
        self.assertFalse(bridge.registered)

    def test_is_active_follows_plugin_switch(self):
        plugin = FakePlugin(FakeContext([]), enabled=False)
        self.assertFalse(CompanionMemoryBridge(plugin).is_active())
        plugin._enabled = True
        self.assertTrue(CompanionMemoryBridge(plugin).is_active())

    async def test_missing_credentials_disable_registered_provider(self):
        context, api = companion_context()
        plugin = FakePlugin(context)
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()
        plugin.config["api_key"] = ""
        self.assertFalse(api.calls[0]["is_active"]())
        self.assertIsNone(await bridge.write(record()))
        self.assertEqual(plugin.client.retained, [])

    async def test_shutdown_without_registration_stops_writes(self):
        bridge = CompanionMemoryBridge(FakePlugin(FakeContext([])))
        await bridge.disconnect()
        self.assertFalse(bridge.is_active())
        self.assertIsNone(await bridge.write(record()))


class BridgeWriteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def _bridge(self, *, enabled: bool = True, config: dict | None = None,
                api_key: str = "key-1") -> CompanionMemoryBridge:
        plugin = FakePlugin(FakeContext([]), enabled=enabled, data_dir=self.data_dir,
                            config=config, api_key=api_key)
        return CompanionMemoryBridge(plugin)

    async def test_write_retains_record_once(self):
        bridge = self._bridge()
        self.assertEqual(await bridge.write(record()), "mem-1")

        call = bridge.plugin.client.retained[0]
        self.assertEqual(call["container_tag"], build_record_container_tag("salt-123", record()))
        self.assertEqual(call["conversation_id"], build_record_conversation_id("salt-123", record()))
        self.assertEqual(call["metadata"]["scope"], "persona")
        self.assertEqual(call["metadata"]["occurred_at"], "2026-10-31T09:00:00+08:00")
        self.assertEqual(call["messages"], [{
            "role": "assistant",
            "content": "Chatbot long-term memory: the character's own diary or dream record\n\n今天写日记：午后在窗边看雨。",
        }])

    async def test_write_is_idempotent_across_instances(self):
        await self._bridge().write(record())
        second = self._bridge()
        self.assertEqual(await second.write(record()), "mem-1")
        self.assertEqual(second.plugin.client.retained, [])

    async def test_write_again_after_account_change(self):
        await self._bridge(api_key="key-1").write(record())
        other = self._bridge(api_key="key-2")
        self.assertEqual(await other.write(record()), "mem-1")
        self.assertEqual(len(other.plugin.client.retained), 1)

    async def test_concurrent_duplicate_writes_only_retain_once(self):
        bridge = self._bridge()
        results = await asyncio.gather(*(bridge.write(record()) for _ in range(5)))
        self.assertEqual(results, ["mem-1"] * 5)
        self.assertEqual(len(bridge.plugin.client.retained), 1)

    async def test_rejects_hard_secret(self):
        bridge = self._bridge()
        self.assertIsNone(await bridge.write(record(content="密码：hunter2hunter2")))
        self.assertEqual(bridge.plugin.client.retained, [])

    async def test_rejects_personal_info_by_default(self):
        bridge = self._bridge()
        self.assertIsNone(await bridge.write(record(content="联系邮箱：companion@example.test")))
        self.assertEqual(bridge.plugin.client.retained, [])

    async def test_personal_info_allowed_when_opted_in(self):
        bridge = self._bridge(config={"companion_retain_personal_sensitive": True})
        self.assertEqual(await bridge.write(record(content="联系邮箱：companion@example.test")), "mem-1")
        self.assertEqual(len(bridge.plugin.client.retained), 1)

    async def test_write_rejects_unknown_kind(self):
        bridge = self._bridge()
        self.assertIsNone(await bridge.write(record(kind="private_chat")))

    async def test_write_rejects_blank_content(self):
        self.assertIsNone(await self._bridge().write(record(content="   ")))

    async def test_write_rejects_missing_memory_id(self):
        self.assertIsNone(await self._bridge().write(record(memory_id="")))

    async def test_write_skipped_when_plugin_disabled(self):
        bridge = self._bridge(enabled=False)
        self.assertIsNone(await bridge.write(record()))
        self.assertEqual(bridge.plugin.client.retained, [])

    async def test_write_survives_client_failure(self):
        bridge = self._bridge()
        bridge.plugin.client.fail = True
        self.assertIsNone(await bridge.write(record()))
        # 失败不记入账本，下一次仍然会尝试
        bridge.plugin.client.fail = False
        self.assertEqual(await bridge.write(record()), "mem-1")

    async def test_failed_write_is_queued_and_retried_on_next_success(self):
        bridge = self._bridge()
        bridge.plugin.client.fail = True
        self.assertIsNone(await bridge.write(record(memory_id="mem-a")))
        self.assertEqual(bridge.plugin.client.retained, [])

        bridge.plugin.client.fail = False
        self.assertEqual(await bridge.write(record(memory_id="mem-b")), "mem-b")
        # mem-a 在同一次调用里被顺带补写（当前这条先写，补写跟在后面）
        self.assertEqual(len(bridge.plugin.client.retained), 2)
        written_ids = [call["metadata"]["memory_id"] for call in bridge.plugin.client.retained]
        self.assertEqual(written_ids, ["mem-b", "mem-a"])

    async def test_crash_leftover_blocks_replay(self):
        # 模拟"写完一条、账本还停在 pending 就崩了"：新实例重放必须跳过，不能写第二遍
        store = WrittenMemoryIdStore(self.data_dir, TARGET)
        store.begin("mem-c")

        bridge = self._bridge()
        self.assertEqual(await bridge.write(record(memory_id="mem-c")), "mem-c")
        self.assertEqual(bridge.plugin.client.retained, [])

    async def test_switching_account_does_not_skip_or_replay_across_targets(self):
        # A 在 account-1 写成功；切到 account-2 之后同一条要重新写，而不是被当成已写过
        await self._bridge(api_key="key-1").write(record(memory_id="mem-a"))
        second = self._bridge(api_key="key-2")
        self.assertEqual(await second.write(record(memory_id="mem-a")), "mem-a")
        self.assertEqual(len(second.plugin.client.retained), 1)

    async def test_switching_account_does_not_replay_failed_queue(self):
        # A 在 account-1 写失败排队；切到 account-2 之后，下一次成功写入不能把 A 补到 account-2
        first = self._bridge(api_key="key-1")
        first.plugin.client.fail = True
        self.assertIsNone(await first.write(record(memory_id="mem-a")))

        second = self._bridge(api_key="key-2")
        self.assertEqual(await second.write(record(memory_id="mem-b")), "mem-b")
        self.assertEqual(second.plugin.client.retained_ids(), ["mem-b"])


class TargetSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    async def test_live_config_switch_keeps_old_queue_in_its_target(self):
        for setting, replacement in (("api_key", "key-2"), ("api_base", "https://proxy.example.com/memory")):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as tmp:
                plugin = FakePlugin(FakeContext([]), data_dir=Path(tmp))
                bridge = CompanionMemoryBridge(plugin)
                old_target = bridge._target
                await bridge.write(record(memory_id="mem-saved"))
                plugin.client.fail = True
                self.assertIsNone(await bridge.write(record(memory_id="mem-queued")))

                plugin.client.fail = False
                plugin.config[setting] = replacement
                self.assertEqual(await bridge.write(record(memory_id="mem-saved")), "mem-saved")
                self.assertEqual(plugin.client.retained_ids(), ["mem-saved", "mem-saved"])
                self.assertNotEqual(bridge._target, old_target)
                self.assertEqual(bridge._pending.peek(), [])
                old_queue = PendingRecordStore(Path(tmp), old_target)
                self.assertEqual([item["record"]["memory_id"] for item in old_queue.peek()], ["mem-queued"])

                # 切回原目标：恢复原队列，已经成功的记录不重复写。
                plugin.config.pop(setting)
                plugin.config["api_key"] = "key-1"
                self.assertEqual(await bridge.write(record(memory_id="mem-saved")), "mem-saved")
                self.assertEqual(plugin.client.retained_ids(), ["mem-saved", "mem-saved", "mem-queued"])

    async def test_target_change_during_client_creation_never_submits_to_new_account(self):
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir)
        bridge = CompanionMemoryBridge(plugin)

        async def changing_client():
            plugin.config["api_key"] = "key-2"
            return plugin.client

        plugin._client = changing_client
        self.assertIsNone(await bridge.write(record(memory_id="old-target")))
        self.assertEqual(plugin.client.retained, [])
        old_queue = PendingRecordStore(self.data_dir, TARGET)
        self.assertEqual([item["record"]["memory_id"] for item in old_queue.peek()], ["old-target"])
        self.assertEqual(await bridge.write(record(memory_id="new-target")), "new-target")
        self.assertEqual(plugin.client.retained_ids(), ["new-target"])

    async def test_target_change_during_inflight_write_defers_new_writes_and_flush(self):
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir)
        bridge = CompanionMemoryBridge(plugin)
        bridge._pending.add(record(memory_id="old-queued"))
        started, release = asyncio.Event(), asyncio.Event()
        original = plugin.client.ingest_conversation

        async def slow_write(**kwargs):
            started.set()
            await release.wait()
            return await original(**kwargs)

        plugin.client.ingest_conversation = slow_write
        task = asyncio.create_task(bridge.write(record(memory_id="inflight")))
        await started.wait()
        plugin.config["api_key"] = "key-2"
        try:
            self.assertIsNone(await bridge.write(record(memory_id="new-account")))
            self.assertEqual(bridge._target, TARGET)
        finally:
            release.set()
            await task
        self.assertEqual(plugin.client.retained_ids(), ["inflight"])
        self.assertEqual(await bridge.write(record(memory_id="new-account")), "new-account")
        self.assertEqual(plugin.client.retained_ids(), ["inflight", "new-account"])
        self.assertEqual([i["record"]["memory_id"] for i in PendingRecordStore(self.data_dir, TARGET).peek()], ["old-queued"])

    async def test_local_target_never_contains_api_key(self):
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir, api_key="test-key-not-for-storage")
        bridge = CompanionMemoryBridge(plugin)
        await bridge.write(record())
        plugin.client.fail = True
        await bridge.write(record(memory_id="failed"))
        for path in self.data_dir.iterdir():
            self.assertNotIn(plugin.config["api_key"], path.read_text())


class HttpBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def _bridge(self, handler):
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir)
        plugin.client = SupermemoryClient(
            api_base="https://api.supermemory.ai",
            api_key="test-key",
            transport=httpx.MockTransport(handler),
            retry_base_delay_seconds=0,
        )
        self.addAsyncCleanup(plugin.client.aclose)
        return CompanionMemoryBridge(plugin)

    async def test_persona_record_reaches_conversations_with_one_isolated_container(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"status": "accepted"})

        bridge = self._bridge(handler)
        payload = record(kind="proactive_message")
        self.assertEqual(await bridge.write(payload), "mem-1")
        self.assertEqual(await bridge.write(payload), "mem-1")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.supermemory.ai/v4/conversations")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.content)
        self.assertEqual(body["containerTags"], [build_record_container_tag("salt-123", payload)])
        self.assertEqual(body["conversationId"], build_record_conversation_id("salt-123", payload))
        self.assertEqual(body["messages"][0]["role"], "assistant")
        self.assertIn("proactive message", body["messages"][0]["content"])
        self.assertEqual(body["metadata"]["scope"], "persona")
        for identity in ("sender-777", "native-1", "inst-1", "conv-1", "bot-1", "host-9"):
            self.assertNotIn(identity, request.content.decode())

    async def test_invalid_success_response_is_not_retried(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, text="not-json")

        bridge = self._bridge(handler)
        self.assertIsNone(await bridge.write(record()))
        self.assertEqual(bridge._pending.peek(), [])
        self.assertEqual(await bridge.write(record()), "mem-1")
        self.assertEqual(len(requests), 1)


class RetryQueueReliabilityTests(unittest.IsolatedAsyncioTestCase):
    """补写队列在取消、超时、结果未知时的行为（维护者复现的三个故障）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def _bridge(self, **kwargs) -> CompanionMemoryBridge:
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir, **kwargs)
        return CompanionMemoryBridge(plugin)

    async def test_cancelled_flush_keeps_unprocessed_records(self):
        bridge = self._bridge()
        bridge._pending.add(record(memory_id="mem-a"))
        bridge._pending.add(record(memory_id="mem-c"))
        bridge.plugin.client.hang_on = {"mem-a"}

        # 宿主在 10 秒超时会取消我们的回调：模拟成一次更短的取消
        # （3.11 起 wait_for 超时抛 TimeoutError，内层仍然是被取消的 CancelledError）
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(bridge.write(record(memory_id="mem-b")), timeout=0.2)

        queued = [item["record"]["memory_id"] for item in bridge._pending.peek(50)]
        # mem-a 卡住的时候 mem-c 还没轮到，取消之后它必须还在队列里
        self.assertIn("mem-c", queued)
        self.assertEqual(bridge.plugin.client.retained_ids(), ["mem-b"])

        bridge.plugin.client.hang_on = set()
        self.assertEqual(await bridge.write(record(memory_id="mem-d")), "mem-d")
        self.assertEqual(bridge.plugin.client.retained_ids(), ["mem-b", "mem-d", "mem-c"])
        self.assertEqual(bridge._pending.peek(50), [])

    async def test_restart_after_cancel_still_sees_queued_records(self):
        bridge = self._bridge()
        bridge._pending.add(record(memory_id="mem-a"))
        bridge._pending.add(record(memory_id="mem-c"))
        bridge.plugin.client.hang_on = {"mem-a"}

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(bridge.write(record(memory_id="mem-b")), timeout=0.2)

        # 进程重启：新实例从文件里读，没处理到的条目必须还在
        restarted = self._bridge()
        queued = [item["record"]["memory_id"] for item in restarted._pending.peek(50)]
        self.assertIn("mem-c", queued)

    async def test_close_during_flush_leaves_rest_queued(self):
        bridge = self._bridge()
        bridge._pending.add(record(memory_id="mem-a"))
        bridge._pending.add(record(memory_id="mem-c"))
        started = asyncio.Event()
        release = asyncio.Event()
        original = bridge.plugin.client.ingest_conversation

        async def slow_first(**kwargs):
            if kwargs["metadata"]["memory_id"] == "mem-a":
                started.set()
                await release.wait()
            return await original(**kwargs)

        bridge.plugin.client.ingest_conversation = slow_first
        task = asyncio.create_task(bridge.write(record(memory_id="mem-b")))
        await started.wait()
        bridge._stopping = True  # 关闭流程开始
        release.set()
        await task

        # mem-a 写完了，mem-c 因为已经在关，留在队列里等下次启动
        queued = [item["record"]["memory_id"] for item in bridge._pending.peek(50)]
        self.assertNotIn("mem-a", queued)
        self.assertIn("mem-c", queued)

    async def test_flush_drops_only_confirmed_items(self):
        bridge = self._bridge()
        bridge._pending.add(record(memory_id="mem-a"))
        bridge._pending.add(record(memory_id="mem-c"))

        original = bridge.plugin.client.ingest_conversation

        async def fail_first(**kwargs):
            if kwargs["metadata"]["memory_id"] == "mem-a":
                raise RuntimeError("supermemory down")
            return await original(**kwargs)

        bridge.plugin.client.ingest_conversation = fail_first
        self.assertEqual(await bridge.write(record(memory_id="mem-b")), "mem-b")

        queued = bridge._pending.peek(50)
        self.assertEqual([item["record"]["memory_id"] for item in queued], ["mem-a"])
        self.assertEqual(queued[0]["attempts"], 2)
        self.assertEqual(bridge.plugin.client.retained_ids(), ["mem-b", "mem-c"])

    async def test_unknown_outcome_is_not_requeued(self):
        bridge = self._bridge()
        bridge.plugin.client.fail = True
        bridge.plugin.client.error = SupermemoryClientError("timed out", kind="timeout")

        self.assertIsNone(await bridge.write(record(memory_id="mem-a")))
        self.assertEqual(bridge._pending.peek(50), [])
        # 账本留成 pending：同一条再来会被当成"可能写过"跳过，不写第二遍
        bridge.plugin.client.fail = False
        self.assertEqual(await bridge.write(record(memory_id="mem-a")), "mem-a")
        self.assertEqual(bridge.plugin.client.retained, [])

    async def test_timeout_during_retry_is_dropped_not_looped(self):
        bridge = self._bridge()
        bridge._pending.add(record(memory_id="mem-a"))
        original = bridge.plugin.client.ingest_conversation

        async def timeout_first(**kwargs):
            if kwargs["metadata"]["memory_id"] == "mem-a":
                raise SupermemoryClientError("timed out", kind="timeout")
            return await original(**kwargs)

        bridge.plugin.client.ingest_conversation = timeout_first
        # 正常写入成功 → 触发补写；补写超时不能再排队，也不能无限重试
        self.assertEqual(await bridge.write(record(memory_id="mem-b")), "mem-b")
        self.assertEqual(bridge._pending.peek(50), [])
        # 结果未知：同一条再来会被当成"可能写过"跳过，不写第二遍
        self.assertEqual(await bridge.write(record(memory_id="mem-a")), "mem-a")
        self.assertEqual(bridge.plugin.client.retained_ids(), ["mem-b"])

    async def test_definite_failure_is_requeued(self):
        bridge = self._bridge()
        bridge.plugin.client.fail = True
        bridge.plugin.client.error = SupermemoryClientError("bad request", status_code=422, kind="http_status")

        self.assertIsNone(await bridge.write(record(memory_id="mem-a")))
        queued = bridge._pending.peek(50)
        self.assertEqual([item["record"]["memory_id"] for item in queued], ["mem-a"])
        # 确定没被接收：撤掉占位，同一条之后还能重试
        bridge.plugin.client.fail = False
        self.assertEqual(await bridge.write(record(memory_id="mem-a")), "mem-a")
        self.assertIn("mem-a", bridge.plugin.client.retained_ids())

    async def test_unwritable_ledger_blocks_remote_write(self):
        bridge = self._bridge()
        bridge._written.path = self.data_dir / "missing-dir" / "ids.json"

        self.assertIsNone(await bridge.write(record(memory_id="mem-a")))
        self.assertEqual(bridge.plugin.client.retained, [])
        # 没有提交远端，所以那条记录还在队列里等账本恢复
        self.assertEqual([item["record"]["memory_id"] for item in bridge._pending.peek(50)], ["mem-a"])

    async def test_flush_budget_limits_batch_size(self):
        import astrbot_plugin_supermemory.companion_bridge as cb
        original = cb.MAX_FLUSH_PER_PASS
        cb.MAX_FLUSH_PER_PASS = 2
        try:
            bridge = self._bridge()
            for index in range(4):
                bridge._pending.add(record(memory_id=f"mem-{index}"))
            self.assertEqual(await bridge.write(record(memory_id="mem-now")), "mem-now")
            self.assertEqual(len(bridge.plugin.client.retained), 3)  # 当前这条 + 预算内 2 条
            self.assertEqual(len(bridge._pending.peek(50)), 2)
        finally:
            cb.MAX_FLUSH_PER_PASS = original


class PayloadIdentityTests(unittest.IsolatedAsyncioTestCase):
    """完整请求载荷里不能出现原始身份标识。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    async def test_request_payload_has_no_raw_identity(self):
        plugin = FakePlugin(FakeContext([]), data_dir=self.data_dir)
        bridge = CompanionMemoryBridge(plugin)

        payload = record(
            memory_id="mem-777",
            kind="proactive_message",
            session_id="private_companion_v2:proactive:sender-777",
        )
        self.assertEqual(await bridge.write(payload), "mem-777")

        call = plugin.client.retained[0]
        blob = json.dumps(call, ensure_ascii=False)
        for raw in ("sender-777", "native-1", "inst-1", "conv-1", "bot-1", "host-9"):
            self.assertNotIn(raw, blob, f"payload leaked {raw}")
        self.assertIn("session_id_hash", call["metadata"])
        self.assertEqual(call["metadata"]["memory_id"], "mem-777")


class PendingRecordStoreTests(unittest.TestCase):
    def test_note_failure_keeps_first_failure_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PendingRecordStore(Path(tmp), TARGET)
            item_id = store.add(record(memory_id="mem-a"))
            first_ts = store.peek(10)[0]["ts"]

            store.note_failure(item_id)

            item = store.peek(10)[0]
            self.assertEqual(item["ts"], first_ts)
            self.assertEqual(item["attempts"], 2)
            self.assertEqual(item["record"]["memory_id"], "mem-a")

    def test_drop_removes_only_named_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PendingRecordStore(Path(tmp), TARGET)
            first = store.add(record(memory_id="mem-a"))
            store.add(record(memory_id="mem-b"))

            store.drop(first)

            self.assertEqual([item["record"]["memory_id"] for item in store.peek(10)], ["mem-b"])

    def test_other_target_items_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PendingRecordStore(Path(tmp), TARGET)
            store.add(record(memory_id="mem-a"))

            other_account = PendingRecordStore(Path(tmp), OTHER_TARGET)
            self.assertEqual(other_account.peek(10), [])

    def test_drop_in_one_target_keeps_other_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = PendingRecordStore(Path(tmp), TARGET)
            item_id = first.add(record(memory_id="mem-a"))
            other_account = PendingRecordStore(Path(tmp), OTHER_TARGET)
            other_account.add(record(memory_id="mem-b"))

            first.drop(item_id)

            # account-2 排队的记录不能被 account-1 的一次删除连带抹掉
            self.assertEqual([i["record"]["memory_id"] for i in other_account.peek(10)], ["mem-b"])

    def test_untargeted_items_are_dropped_not_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PendingRecordStore(Path(tmp), TARGET)
            store.path.write_text(
                json.dumps([{"ts": __import__("time").time(), "record": dict(record())}]),
                encoding="utf-8",
            )
            # 上一版没有目标信息：不猜它属于哪个 account／服务地址
            self.assertEqual(store.peek(10), [])

    def test_expired_items_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PendingRecordStore(Path(tmp), TARGET)
            store.add(record(memory_id="mem-a"))
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            raw[0]["ts"] = 1.0  # 远超 TTL
            store.path.write_text(json.dumps(raw), encoding="utf-8")

            self.assertEqual(store.peek(10), [])


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_waits_for_inflight_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        context, api = companion_context()
        plugin = FakePlugin(context, data_dir=Path(tmp.name))
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()

        started = asyncio.Event()
        release = asyncio.Event()
        original = plugin.client.ingest_conversation

        async def slow_retain(**kwargs):
            started.set()
            await release.wait()
            return await original(**kwargs)

        plugin.client.ingest_conversation = slow_retain
        task = asyncio.create_task(bridge.write(record()))
        await started.wait()

        disconnect = asyncio.create_task(bridge.disconnect())
        await asyncio.sleep(0.05)
        self.assertFalse(disconnect.done(), "disconnect 应先等待在途写入")
        # 关闭一开始就不再自称可用：注册表不会把新写入派过来
        self.assertFalse(bridge.is_active())

        release.set()
        await task
        await disconnect
        self.assertEqual(len(api.unregistered), 1)

    async def test_disconnect_waits_for_flush_behind_main_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        context, api = companion_context()
        plugin = FakePlugin(context, data_dir=Path(tmp.name))
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()
        bridge._pending.add(record(memory_id="mem-old"))

        started = asyncio.Event()
        release = asyncio.Event()
        original = plugin.client.ingest_conversation

        async def slow_retain(**kwargs):
            if kwargs["metadata"]["memory_id"] == "mem-old":
                started.set()
                await release.wait()
            return await original(**kwargs)

        plugin.client.ingest_conversation = slow_retain
        task = asyncio.create_task(bridge.write(record(memory_id="mem-new")))
        await started.wait()

        disconnect = asyncio.create_task(bridge.disconnect())
        await asyncio.sleep(0.05)
        # 主写入已经结束，但补写还在跑：disconnect 必须等它，否则 terminate 会关掉还在用的客户端
        self.assertFalse(disconnect.done())

        release.set()
        await task
        await disconnect
        self.assertEqual(len(api.unregistered), 1)

    async def test_failed_unregister_is_not_selectable_and_retried(self):
        api = FakeExtensionApi(fail_unregister=True)
        context, _ = companion_context(api)
        plugin = FakePlugin(context, data_dir=Path(tempfile.mkdtemp()))
        bridge = CompanionMemoryBridge(plugin)
        await bridge.ensure_registered()

        await bridge.disconnect()
        self.assertTrue(bridge.registered, "注销失败要保留引用，等下次重试")
        self.assertFalse(bridge.is_active(), "注销失败的注册不能继续被选中")

        api.fail_unregister = False
        self.assertTrue(await bridge.ensure_registered())
        self.assertEqual(len(api.unregistered), 1, "下一次 ensure_registered 要再试一次注销")
        self.assertTrue(bridge.is_active())


class WrittenMemoryIdStoreTests(unittest.TestCase):
    def test_round_trip_and_target_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WrittenMemoryIdStore(Path(tmp), TARGET)
            self.assertEqual(store.begin("a"), BEGIN_RESERVED)
            store.commit("a")
            reloaded = WrittenMemoryIdStore(Path(tmp), TARGET)
            self.assertTrue(reloaded.contains("a"))
            # 同一个 account 换服务地址：不同目标，不能算写过
            other_host = WrittenMemoryIdStore(Path(tmp), TARGET.replace("api.supermemory.ai", "other.example.com"))
            self.assertFalse(other_host.contains("a"))
            # 同一个服务地址换 account：同样不能算写过
            other_account = WrittenMemoryIdStore(Path(tmp), OTHER_TARGET)
            self.assertFalse(other_account.contains("a"))

    def test_begin_twice_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WrittenMemoryIdStore(Path(tmp), TARGET)
            self.assertEqual(store.begin("a"), BEGIN_RESERVED)
            # 第一次没提交就再来一次：说明上次可能已经写过，必须跳过
            self.assertEqual(store.begin("a"), BEGIN_SKIP)

    def test_abort_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WrittenMemoryIdStore(Path(tmp), TARGET)
            store.begin("a")
            store.abort("a")
            self.assertEqual(store.begin("a"), BEGIN_RESERVED)

    def test_unwritable_ledger_refuses_to_reserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WrittenMemoryIdStore(Path(tmp), TARGET)
            store.path = Path(tmp) / "missing-dir" / "ids.json"

            self.assertEqual(store.begin("a"), BEGIN_UNAVAILABLE)
            # 占位没有留在内存里：账本修好之后这条还能重新占位
            self.assertFalse(store.contains("a"))

    def test_legacy_list_format_is_not_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companion_written_ids.json"
            path.write_text('["legacy-1", "legacy-2"]', encoding="utf-8")
            store = WrittenMemoryIdStore(Path(tmp), TARGET)
            # 旧格式没有 account 信息，宁可不认，也不把记录写到错的 account
            self.assertFalse(store.contains("legacy-1"))

    def test_cap_evicts_oldest(self):
        import astrbot_plugin_supermemory.companion_bridge as cb
        original = cb.MAX_WRITTEN_IDS
        cb.MAX_WRITTEN_IDS = 20
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = WrittenMemoryIdStore(Path(tmp), TARGET)
                for index in range(25):
                    store.begin(f"id-{index}")
                    store.commit(f"id-{index}")
                reloaded = WrittenMemoryIdStore(Path(tmp), TARGET)
                self.assertTrue(reloaded.contains("id-24"))
                self.assertFalse(reloaded.contains("id-0"))
        finally:
            cb.MAX_WRITTEN_IDS = original


if __name__ == "__main__":
    unittest.main()
