import ast
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
            "recall_item_max_chars",
            "memory_extract_max_depth",
            "search_threshold",
            "search_mode",
            "retain_enabled",
            "retain_decision_mode",
            "retain_min_chars",
            "retain_sensitive_requires_explicit",
            "memory_ai_enabled",
            "memory_ai_provider_id",
            "memory_ai_fallback_to_current_provider",
            "memory_ai_min_confidence",
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
        self.assertEqual(config["recall_item_max_chars"]["default"], 360)
        self.assertEqual(config["memory_extract_max_depth"]["default"], 4)
        self.assertEqual(config["search_mode"]["default"], "memories")
        self.assertEqual(config["retain_decision_mode"]["default"], "balanced")
        self.assertEqual(config["retain_min_chars"]["default"], 8)
        self.assertIs(config["retain_sensitive_requires_explicit"]["default"], True)
        self.assertIs(config["memory_ai_enabled"]["default"], False)
        self.assertEqual(config["memory_ai_provider_id"]["default"], "")
        self.assertEqual(config["memory_ai_provider_id"]["_special"], "select_provider")
        self.assertIs(config["memory_ai_fallback_to_current_provider"]["default"], True)
        self.assertEqual(config["memory_ai_min_confidence"]["default"], 0.7)
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

    def test_memory_ai_uses_selectable_llm_provider_and_keeps_legacy_compat(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        memory_ai = (ROOT / "memory_ai.py").read_text(encoding="utf-8")

        self.assertIn("memory_ai_enabled", main)
        self.assertIn("memory_ai_provider_id", main)
        self.assertIn("memory_ai_fallback_to_current_provider", main)
        self.assertIn("retain_ai_provider_id", memory_ai)
        self.assertIn("get_current_chat_provider_id", main)
        self.assertIn("llm_generate", main)
        self.assertIn("chat_provider_id", main)
        self.assertNotIn("text_chat", main)
        self.assertNotIn("get_using_provider", main)

    def test_main_uses_config_aware_async_client_factory(self):
        tree = _main_tree()
        client_method = _class_method(tree, "SupermemoryPlugin", "_client")

        self.assertIsNotNone(client_method)
        self.assertTrue(_has_attribute(client_method, "supermemory_client_signature"))
        self.assertTrue(_calls_method(client_method, "_client_signature"))
        self.assertTrue(_awaits_aclose(client_method, "supermemory_client"))
        self.assertTrue(_instantiates(client_method, "SupermemoryClient"))


def _main_tree():
    return ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))


def _class_method(tree, class_name, method_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)) and item.name == method_name:
                    return item
    return None


def _has_attribute(node, attr_name):
    return any(isinstance(item, ast.Attribute) and item.attr == attr_name for item in ast.walk(node))


def _calls_method(node, method_name):
    return any(
        isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == method_name
        for item in ast.walk(node)
    )


def _awaits_aclose(node, client_attr):
    for item in ast.walk(node):
        if not isinstance(item, ast.Await) or not isinstance(item.value, ast.Call):
            continue
        func = item.value.func
        if not isinstance(func, ast.Attribute) or func.attr != "aclose":
            continue
        value = func.value
        if isinstance(value, ast.Attribute) and value.attr == client_attr:
            return True
    return False


def _instantiates(node, class_name):
    return any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == class_name for item in ast.walk(node))


if __name__ == "__main__":
    unittest.main()
