import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticContractTests(unittest.TestCase):
    def test_metadata_contract(self):
        metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")

        self.assertIn("name: astrbot_plugin_supermemory", metadata)
        self.assertIn("display_name: Supermemory", metadata)
        self.assertIn("astrbot_version:", metadata)

    def test_config_contract(self):
        config = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        for key in (
            "enabled",
            "api_base",
            "api_key",
            "enable_private_memory",
            "enable_group_memory",
            "enable_group_shared_memory",
            "recall_limit",
            "search_threshold",
            "search_mode",
            "retain_enabled",
            "retain_decision_mode",
            "retain_min_chars",
            "retain_sensitive_requires_explicit",
            "retain_ai_enabled",
            "retain_ai_provider_id",
            "retain_ai_fallback_to_current_provider",
            "retain_ai_min_confidence",
            "retain_dedupe_enabled",
            "retain_dedupe_threshold",
            "retain_dedupe_limit",
            "retain_write_raw_conversation",
            "retain_user_message",
            "retain_assistant_message",
            "request_timeout_seconds",
        ):
            self.assertIn(key, config)
        self.assertEqual(config["api_base"]["default"], "https://api.supermemory.ai")
        self.assertEqual(config["search_mode"]["default"], "memories")
        self.assertEqual(config["retain_decision_mode"]["default"], "balanced")
        self.assertEqual(config["retain_min_chars"]["default"], 8)
        self.assertIs(config["retain_sensitive_requires_explicit"]["default"], True)
        self.assertIs(config["retain_ai_enabled"]["default"], False)
        self.assertEqual(config["retain_ai_provider_id"]["default"], "")
        self.assertEqual(config["retain_ai_provider_id"]["_special"], "select_provider")
        self.assertIs(config["retain_ai_fallback_to_current_provider"]["default"], False)
        self.assertEqual(config["retain_ai_min_confidence"]["default"], 0.7)
        self.assertIs(config["retain_dedupe_enabled"]["default"], True)
        self.assertEqual(config["retain_dedupe_threshold"]["default"], 0.85)
        self.assertEqual(config["retain_dedupe_limit"]["default"], 5)
        self.assertIs(config["retain_write_raw_conversation"]["default"], False)

    def test_command_group_contract(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('@filter.command_group("supermemory")', main)
        self.assertIn("build_scopes_from_event", main)
        self.assertIn("from .retention_policy import", main)
        self.assertIn("scopes.recall_scopes", main)
        self.assertIn("scopes.retain_scopes", main)
        for command in ("status", "recall", "on", "off", "help"):
            self.assertIn(f'@supermemory.command("{command}")', main)

    def test_ai_retention_uses_selectable_llm_provider(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("retain_ai_provider_id", main)
        self.assertIn("get_current_chat_provider_id", main)
        self.assertIn("llm_generate", main)
        self.assertIn("chat_provider_id", main)
        self.assertNotIn("text_chat", main)
        self.assertNotIn("get_using_provider", main)

    def test_main_uses_config_aware_async_client_factory(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("async def _client(self) -> SupermemoryClient:", main)
        self.assertIn("self.supermemory_client_signature", main)
        self.assertIn("await self.supermemory_client.aclose()", main)
        self.assertNotIn("self.supermemory_client = SupermemoryClient(\n            api_base=str(self.config.get", main)


if __name__ == "__main__":
    unittest.main()
