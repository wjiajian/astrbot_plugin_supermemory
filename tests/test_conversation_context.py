import unittest

from conversation_context import recent_turns, short_query_with_context


class ConversationContextTests(unittest.TestCase):
    def test_keeps_last_turns_of_user_and_assistant_text(self):
        contexts = [
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "回复一"},
            {"role": "user", "content": "第二句"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "回复二"},
        ]

        self.assertEqual(
            recent_turns(contexts, max_turns=1),
            [{"role": "user", "text": "第二句"}, {"role": "assistant", "text": "回复二"}],
        )

    def test_flattens_multimodal_text_parts_and_truncates(self):
        contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看看这张图"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "text", "text": "  我养的猫  "},
                ],
            },
            {"role": "assistant", "content": "很" * 50},
        ]

        result = recent_turns(contexts, max_turns=3, max_chars_per_message=10)

        self.assertEqual(result[0], {"role": "user", "text": "看看这张图 我养的猫"})
        self.assertEqual(result[1]["text"], "很" * 10 + "…")

    def test_invalid_or_disabled_input_returns_empty(self):
        self.assertEqual(recent_turns(None), [])
        self.assertEqual(recent_turns("not a list"), [])
        self.assertEqual(recent_turns([{"role": "user", "content": "hi"}], max_turns=0), [])
        self.assertEqual(recent_turns(["bad", {"role": "user", "content": None}]), [])

    def test_short_query_borrows_last_user_message(self):
        recent = [
            {"role": "user", "text": "我在考虑用 Redis 还是 Kafka"},
            {"role": "assistant", "text": "各有优劣"},
        ]

        self.assertEqual(short_query_with_context("那前者呢？", recent), "我在考虑用 Redis 还是 Kafka 那前者呢？")

    def test_long_query_or_missing_context_adds_nothing(self):
        recent = [{"role": "user", "text": "上一句"}]

        self.assertEqual(short_query_with_context("我想知道上次我们讨论的项目技术栈是什么", recent), "")
        self.assertEqual(short_query_with_context("那个呢", [{"role": "assistant", "text": "x"}]), "")
        self.assertEqual(short_query_with_context("", recent), "")


if __name__ == "__main__":
    unittest.main()
