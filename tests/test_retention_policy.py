import unittest

from retention_policy import apply_ai_retention_result, dedupe_action, decide_retention


class RetentionPolicyTests(unittest.TestCase):
    def test_skips_commands_chitchat_and_short_text(self):
        self.assertFalse(_decide("/supermemory status").should_retain)
        self.assertFalse(_decide("谢谢").should_retain)
        self.assertFalse(_decide("abc").should_retain)

    def test_explicit_private_memory_is_retained(self):
        decision = _decide("记住我喜欢简洁回答", primary_scope_type="private")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.reason, "explicit_memory")
        self.assertTrue(decision.keep_user)
        self.assertFalse(decision.keep_assistant)
        self.assertEqual(decision.target_scope_types, ("private",))
        self.assertEqual(decision.memory_text, "用户喜欢简洁回答")
        self.assertEqual(decision.memory_type, "preference")

    def test_explicit_group_personal_memory_targets_member_scope(self):
        decision = _decide("记住我喜欢简洁回答", primary_scope_type="group_member")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.target_scope_types, ("group_member",))

    def test_group_public_rule_targets_shared_scope(self):
        decision = _decide("本群规则是提问时先贴日志", primary_scope_type="group_member")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.target_scope_types, ("group_shared",))

    def test_group_project_agreement_targets_shared_scope(self):
        decision = _decide("我们项目约定是使用 Python 3.12", primary_scope_type="group_member")

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.target_scope_types, ("group_shared",))

    def test_group_personal_preference_does_not_target_shared_scope(self):
        decision = _decide("我喜欢用中文回答技术问题", primary_scope_type="group_member")

        self.assertTrue(decision.should_retain)
        self.assertNotIn("group_shared", decision.target_scope_types)
        self.assertEqual(decision.target_scope_types, ("group_member",))

    def test_hard_sensitive_secrets_are_never_retained(self):
        for text in (
            "记住我的 API key 是 abc123",
            "记住 password 是 hunter2",
            "记住我的密钥是 abc123",
        ):
            with self.subTest(text=text):
                decision = _decide(text, config={"retain_decision_mode": "all"})
                self.assertFalse(decision.should_retain)
                self.assertEqual(decision.reason, "hard_sensitive")

    def test_personal_sensitive_requires_explicit_memory_intent(self):
        implicit = _decide("我的邮箱是 alice@example.com")
        explicit = _decide("记住我的邮箱是 alice@example.com")

        self.assertFalse(implicit.should_retain)
        self.assertEqual(implicit.reason, "personal_sensitive_requires_explicit")
        self.assertTrue(explicit.should_retain)
        self.assertEqual(explicit.sensitivity, "personal")

    def test_ai_result_refines_memory_text_and_scope(self):
        base = _decide("本群规则是提问时先贴日志", primary_scope_type="group_member")
        decision = apply_ai_retention_result(
            base,
            '{"should_retain": true, "memory_text": "本群规则：提问时先贴日志。", '
            '"scope": "group_shared", "confidence": 0.9, "reason": "rule", "sensitivity": "low"}',
            "group_member",
            {},
        )

        self.assertIsNotNone(decision)
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.memory_text, "本群规则：提问时先贴日志。")
        self.assertEqual(decision.target_scope_types, ("group_shared",))
        self.assertEqual(decision.source, "ai")

    def test_ai_low_confidence_skips_retention(self):
        base = _decide("记住我喜欢简洁回答")
        decision = apply_ai_retention_result(
            base,
            '{"should_retain": true, "memory_text": "用户喜欢简洁回答。", '
            '"scope": "private", "confidence": 0.2, "reason": "preference", "sensitivity": "low"}',
            "private",
            {"retain_ai_min_confidence": 0.7},
        )

        self.assertIsNotNone(decision)
        self.assertFalse(decision.should_retain)
        self.assertEqual(decision.reason, "ai_low_confidence")

    def test_dedupe_action_skips_duplicate_but_keeps_correction(self):
        self.assertEqual(
            dedupe_action("用户喜欢简洁回答", ["用户喜欢简洁回答。"], 0.85, "preference"),
            "duplicate",
        )
        self.assertEqual(
            dedupe_action("用户不是喜欢长回答，是喜欢简洁回答", ["用户喜欢长回答"], 0.85, "correction"),
            "correction",
        )

    def test_all_mode_keeps_old_broad_write_behavior(self):
        decision = _decide(
            "谢谢",
            assistant_text="不客气",
            primary_scope_type="group_member",
            config={"retain_decision_mode": "all"},
        )

        self.assertTrue(decision.should_retain)
        self.assertTrue(decision.keep_user)
        self.assertTrue(decision.keep_assistant)
        self.assertEqual(decision.target_scope_types, ("group_shared", "group_member"))

    def test_strict_mode_only_accepts_explicit_memory(self):
        implicit = _decide("我的项目使用 FastAPI", config={"retain_decision_mode": "strict"})
        explicit = _decide("记住我的项目使用 FastAPI", config={"retain_decision_mode": "strict"})

        self.assertFalse(implicit.should_retain)
        self.assertEqual(implicit.reason, "strict_requires_explicit")
        self.assertTrue(explicit.should_retain)


def _decide(
    user_text: str,
    assistant_text: str = "好的，我记住了",
    primary_scope_type: str = "private",
    config: dict | None = None,
):
    return decide_retention(user_text, assistant_text, primary_scope_type, config or {})


if __name__ == "__main__":
    unittest.main()
