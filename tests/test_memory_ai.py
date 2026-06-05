import unittest

from memory_ai import (
    build_recall_query_prompt,
    memory_ai_enabled,
    memory_ai_fallback_to_current_provider,
    memory_ai_min_confidence,
    memory_ai_provider_id,
    parse_recall_queries,
)


class MemoryAiTests(unittest.TestCase):
    def test_new_memory_ai_config_overrides_legacy_retain_ai_config(self):
        config = {
            "memory_ai_enabled": False,
            "memory_ai_provider_id": "memory-small",
            "memory_ai_fallback_to_current_provider": False,
            "memory_ai_min_confidence": 0.82,
            "retain_ai_enabled": True,
            "retain_ai_provider_id": "legacy",
            "retain_ai_fallback_to_current_provider": True,
            "retain_ai_min_confidence": 0.2,
        }

        self.assertFalse(memory_ai_enabled(config))
        self.assertEqual(memory_ai_provider_id(config), "memory-small")
        self.assertFalse(memory_ai_fallback_to_current_provider(config))
        self.assertEqual(memory_ai_min_confidence(config), 0.82)

    def test_legacy_retain_ai_config_is_read_when_new_keys_are_absent(self):
        config = {
            "retain_ai_enabled": "true",
            "retain_ai_provider_id": "legacy-small",
            "retain_ai_fallback_to_current_provider": "false",
            "retain_ai_min_confidence": "0.91",
        }

        self.assertTrue(memory_ai_enabled(config))
        self.assertEqual(memory_ai_provider_id(config), "legacy-small")
        self.assertFalse(memory_ai_fallback_to_current_provider(config))
        self.assertEqual(memory_ai_min_confidence(config), 0.91)

    def test_defaults_match_memory_ai_schema(self):
        self.assertFalse(memory_ai_enabled({}))
        self.assertEqual(memory_ai_provider_id({}), "")
        self.assertTrue(memory_ai_fallback_to_current_provider({}))
        self.assertEqual(memory_ai_min_confidence({}), 0.7)

    def test_recall_query_parser_keeps_original_first_dedupes_and_limits(self):
        queries = parse_recall_queries(
            'noise {"queries": ["我喜欢什么饮料", "饮料 偏好", "饮料 偏好", "咖啡", "冰美式"], "reason": "expand"}',
            "我喜欢什么饮料",
            max_queries=4,
        )

        self.assertEqual(queries, ["我喜欢什么饮料", "饮料 偏好", "咖啡", "冰美式"])

    def test_recall_query_parser_falls_back_to_original_query_on_invalid_json(self):
        self.assertEqual(parse_recall_queries("not-json", "项目约定是什么"), ["项目约定是什么"])

    def test_recall_prompt_requires_json_without_answering_user(self):
        prompt = build_recall_query_prompt("我喜欢什么饮料")

        self.assertIn("Return one JSON object only", prompt)
        self.assertIn("Do not answer the user", prompt)
        self.assertIn('"user_query"', prompt)


if __name__ == "__main__":
    unittest.main()
