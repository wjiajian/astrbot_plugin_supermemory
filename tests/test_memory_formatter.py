import unittest

from memory_formatter import (
    extract_memories,
    extract_results,
    format_recall_results,
    format_search_results,
    merge_ranked_memories,
)


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

    def test_merge_ranked_memories_interleaves_lists_instead_of_concatenating(self):
        first = [{"memory": f"A{i}"} for i in range(6)]
        second = [{"memory": "B0"}, {"memory": "B1"}]

        merged = merge_ranked_memories([first, second])

        self.assertEqual([item["memory"] for item in merged[:4]], ["A0", "B0", "A1", "B1"])
        self.assertEqual(len(merged), 8)

    def test_merge_ranked_memories_boosts_memories_found_by_several_lists(self):
        merged = merge_ranked_memories(
            [
                [{"memory": "only first"}, {"memory": "shared"}],
                [{"memory": "only second"}, {"memory": " Shared "}],
            ]
        )

        self.assertEqual(merged[0]["memory"], "shared")
        self.assertEqual(len(merged), 3)

    def test_prefixes_local_date(self):
        raw = [
            {"memory": "用户计划考雅思。", "date": "2026-10-30", "updatedAt": "2026-10-24T02:00:00"},
            {"memory": "本群提问先贴日志。", "updatedAt": "2026-10-20T12:00:00"},
            {"memory": "坏日期。", "updatedAt": "not-a-date"},
            {"memory": "UTC 日期。", "createdAt": "2026-10-24T02:00:00Z"},
        ]

        lines = format_recall_results(raw, 5).splitlines()[1:-1]

        self.assertEqual(lines[0], "- [2026-10-30] 用户计划考雅思。")
        self.assertEqual(lines[1], "- [2026-10-20] 本群提问先贴日志。")
        self.assertEqual(lines[2], "- 坏日期。")
        self.assertRegex(lines[3], r"^- \[2026-10-2[345]\] UTC 日期。$")


if __name__ == "__main__":
    unittest.main()
