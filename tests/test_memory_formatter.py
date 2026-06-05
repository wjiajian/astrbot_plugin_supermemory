import unittest

from memory_formatter import extract_memories, extract_results, format_recall_results, format_search_results


class MemoryFormatterTests(unittest.TestCase):
    def test_formats_memory_results(self):
        formatted = format_recall_results(
            {"results": [{"memory": "Alice likes tea."}, {"memory": "Alice works remotely."}]},
            limit=5,
        )

        self.assertEqual(
            formatted,
            '<supermemory_context scope="memory">\n- Alice likes tea.\n- Alice works remotely.\n</supermemory_context>',
        )

    def test_formats_named_scope(self):
        formatted = format_recall_results({"results": [{"memory": "Public group fact"}]}, limit=5, title="group_shared")

        self.assertTrue(formatted.startswith('<supermemory_context scope="group_shared">'))

    def test_formats_chunk_results(self):
        formatted = format_recall_results({"results": [{"chunk": "Chunk content"}]}, limit=5)

        self.assertIn("- Chunk content", formatted)

    def test_supports_nested_memory_shape(self):
        formatted = format_recall_results({"results": [{"observation": {"content": "Nested fact"}}]}, limit=5)

        self.assertIn("- Nested fact", formatted)

    def test_max_extract_depth_can_be_configured(self):
        raw = {"results": [{"observation": {"result": {"content": "Nested too deep by default"}}}]}

        self.assertEqual(format_recall_results(raw, limit=5, max_extract_depth=1), "")
        self.assertIn("Nested too deep by default", format_recall_results(raw, limit=5, max_extract_depth=2))

    def test_limits_count_and_truncates_text(self):
        formatted = format_recall_results(
            {"results": [{"memory": "a" * 20}, {"memory": "second"}]},
            limit=1,
            item_max_chars=10,
        )

        self.assertIn("- aaaaaaa...", formatted)
        self.assertNotIn("second", formatted)

    def test_extract_memories_handles_unexpected_shapes(self):
        self.assertEqual(extract_memories(None), [])
        self.assertEqual(extract_memories("bad"), [])
        self.assertEqual(extract_memories({"data": ["ok"]}), ["ok"])

    def test_keeps_search_formatter_aliases(self):
        self.assertEqual(extract_results({"data": ["ok"]}), ["ok"])
        self.assertEqual(
            format_search_results({"results": [{"memory": "Alias works."}]}, limit=5),
            format_recall_results({"results": [{"memory": "Alias works."}]}, limit=5),
        )


if __name__ == "__main__":
    unittest.main()
