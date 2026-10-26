import unittest

from retention_policy import (
    apply_ai_retention_results,
    build_ai_retention_prompt,
    dedupe_action,
    decide_retention,
    is_trivial_message,
    precheck_ai_retention,
)


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

    def test_ai_precheck_allows_normal_candidates_without_rule_classification(self):
        decision = precheck_ai_retention(
            "下午把订单同步模块迁到了 Celery worker",
            "好的",
            "private",
            {},
        )

        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.reason, "ai_candidate")
        self.assertEqual(decision.source, "ai")

    def test_ai_precheck_still_blocks_hard_sensitive_secrets(self):
        decision = precheck_ai_retention("我的 token 是 abc123456789", "", "private", {})

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
        decision = _first(apply_ai_retention_results(
            base,
            '{"should_retain": true, "memory_text": "本群规则：提问时先贴日志。", '
            '"scope": "group_shared", "confidence": 0.9, "reason": "rule", "sensitivity": "low"}',
            "group_member",
            {},
        ))

        self.assertIsNotNone(decision)
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.memory_text, "本群规则：提问时先贴日志。")
        self.assertEqual(decision.target_scope_types, ("group_shared",))
        self.assertEqual(decision.source, "ai")

    def test_ai_result_uses_memory_type_field(self):
        base = precheck_ai_retention("我们项目使用 Python 3.12", "", "group_member", {})
        decision = _first(apply_ai_retention_results(
            base,
            '{"should_retain": true, "memory_text": "项目使用 Python 3.12。", '
            '"scope": "group_shared", "memory_type": "project", "confidence": 0.95, '
            '"reason": "shared technical fact", "sensitivity": "low"}',
            "group_member",
            {},
        ))

        self.assertIsNotNone(decision)
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.memory_type, "project")
        self.assertEqual(decision.reason, "ai_project")

    def test_ai_result_can_reject_retention(self):
        base = precheck_ai_retention("今天天气不错", "", "private", {})
        decision = _first(apply_ai_retention_results(
            base,
            '{"should_retain": false, "memory_text": "", "scope": "private", '
            '"memory_type": "conversation", "confidence": 0.99, "sensitivity": "low", "reason": "temporary"}',
            "private",
            {},
        ))

        self.assertIsNotNone(decision)
        self.assertFalse(decision.should_retain)
        self.assertEqual(decision.reason, "ai_rejected")

    def test_ai_low_confidence_skips_retention(self):
        base = _decide("记住我喜欢简洁回答")
        decision = _first(apply_ai_retention_results(
            base,
            '{"should_retain": true, "memory_text": "用户喜欢简洁回答。", '
            '"scope": "private", "confidence": 0.2, "reason": "preference", "sensitivity": "low"}',
            "private",
            {"retain_ai_min_confidence": 0.7},
        ))

        self.assertIsNotNone(decision)
        self.assertFalse(decision.should_retain)
        self.assertEqual(decision.reason, "ai_low_confidence")

    def test_memory_ai_min_confidence_overrides_legacy_threshold(self):
        base = _decide("记住我喜欢简洁回答")
        decision = _first(apply_ai_retention_results(
            base,
            '{"should_retain": true, "memory_text": "用户喜欢简洁回答。", '
            '"scope": "private", "memory_type": "preference", "confidence": 0.75, "sensitivity": "low"}',
            "private",
            {"memory_ai_min_confidence": 0.8, "retain_ai_min_confidence": 0.1},
        ))

        self.assertIsNotNone(decision)
        self.assertFalse(decision.should_retain)
        self.assertEqual(decision.reason, "ai_low_confidence")

    def test_ai_json_object_can_follow_noisy_prefix(self):
        base = _decide("记住我喜欢简洁回答")
        decision = _first(apply_ai_retention_results(
            base,
            'noise before json {bad} {"should_retain": true, "memory_text": "用户喜欢简洁回答。", '
            '"scope": "private", "confidence": 0.9, "reason": "preference", "sensitivity": "low"}',
            "private",
            {},
        ))

        self.assertIsNotNone(decision)
        self.assertTrue(decision.should_retain)
        self.assertEqual(decision.memory_text, "用户喜欢简洁回答。")

    def test_invalid_ai_json_returns_none_for_rule_fallback(self):
        base = _decide("记住我喜欢简洁回答")

        self.assertIsNone(apply_ai_retention_results(base, "not-json", "private", {}))

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

    def test_ai_result_supports_multiple_memories(self):
        base = precheck_ai_retention("我下周五要考雅思，最近在刷听力", "加油！", "private", {})
        decisions = apply_ai_retention_results(
            base,
            '{"memories": ['
            '{"text": "用户计划在2026-10-30参加雅思考试。", "scope": "private", "memory_type": "event", '
            '"confidence": 0.9, "sensitivity": "low"},'
            '{"text": "用户最近在练习雅思听力。", "scope": "private", "memory_type": "event", '
            '"confidence": 0.85, "sensitivity": "low"},'
            '{"text": "用户最近在练习雅思听力。", "scope": "private", "memory_type": "event", '
            '"confidence": 0.85, "sensitivity": "low"}'
            '], "reason": "life events"}',
            "private",
            {},
        )

        self.assertEqual(
            [decision.memory_text for decision in decisions],
            ["用户计划在2026-10-30参加雅思考试。", "用户最近在练习雅思听力。"],
        )
        self.assertTrue(all(decision.should_retain for decision in decisions))
        self.assertEqual({decision.memory_type for decision in decisions}, {"event"})
        self.assertEqual(decisions[0].reason, "ai_event")

    def test_ai_multiple_memories_filters_low_confidence_and_secrets_per_item(self):
        base = precheck_ai_retention("我喜欢简洁回答，我的 token 不重要", "", "private", {})
        decisions = apply_ai_retention_results(
            base,
            '{"memories": ['
            '{"text": "用户喜欢简洁回答。", "memory_type": "preference", "confidence": 0.9},'
            '{"text": "用户可能喜欢猫。", "memory_type": "preference", "confidence": 0.3},'
            '{"text": "token: abcdefgh12345", "memory_type": "profile", "confidence": 0.9}'
            ']}',
            "private",
            {},
        )

        self.assertEqual([decision.memory_text for decision in decisions], ["用户喜欢简洁回答。"])

    def test_ai_empty_memories_list_rejects(self):
        base = precheck_ai_retention("Python 的 GIL 是什么", "GIL 是全局解释器锁……", "private", {})
        decisions = apply_ai_retention_results(base, '{"memories": [], "reason": "general knowledge"}', "private", {})

        self.assertEqual(len(decisions), 1)
        self.assertFalse(decisions[0].should_retain)
        self.assertEqual(decisions[0].reason, "ai_rejected")

    def test_ai_all_low_confidence_reports_low_confidence(self):
        base = precheck_ai_retention("随便聊聊", "", "private", {})
        decisions = apply_ai_retention_results(
            base,
            '{"memories": [{"text": "用户在随便聊聊。", "confidence": 0.1}]}',
            "private",
            {},
        )

        self.assertEqual([decision.reason for decision in decisions], ["ai_low_confidence"])

    def test_ai_json_without_known_shape_falls_back(self):
        base = precheck_ai_retention("我喜欢简洁回答", "", "private", {})

        self.assertIsNone(apply_ai_retention_results(base, '{"answer": "hi"}', "private", {}))

    def test_ai_prompt_excludes_assistant_knowledge_and_includes_time(self):
        prompt = build_ai_retention_prompt("Python 的 GIL 是什么", "解释" * 2000, "private", "2026-10-24T10:00+08:00")

        self.assertIn('"memories"', prompt)
        self.assertIn("General knowledge", prompt)
        self.assertIn("assistant_text is context only", prompt)
        self.assertIn("2026-10-24T10:00+08:00", prompt)
        self.assertIn("event", prompt)
        self.assertLess(prompt.count("解释"), 1300)

    def test_ai_prompt_includes_recent_context_and_current_turn_rule(self):
        prompt = build_ai_retention_prompt(
            "就用前者吧",
            "好的",
            "group_member",
            "2026-10-24T10:00+08:00",
            [{"role": "user", "text": "我在考虑用 Redis 还是 Kafka"}],
        )

        self.assertIn('"recent_context"', prompt)
        self.assertIn("我在考虑用 Redis 还是 Kafka", prompt)
        self.assertIn("Only extract from the CURRENT turn", prompt)
        self.assertIn("never attribute their facts", prompt)
        self.assertIn("account identifiers", prompt)

    def test_is_trivial_message(self):
        for text in ("", "  ", "谢谢", "好的呀", "哈哈哈", "/supermemory status", "ok"):
            with self.subTest(text=text):
                self.assertTrue(is_trivial_message(text))
        for text in ("我喜欢什么饮料", "那前者呢？"):
            with self.subTest(text=text):
                self.assertFalse(is_trivial_message(text))


def _first(decisions):
    assert decisions, "expected at least one decision"
    return decisions[0]


def _decide(
    user_text: str,
    assistant_text: str = "好的，我记住了",
    primary_scope_type: str = "private",
    config: dict | None = None,
):
    return decide_retention(user_text, assistant_text, primary_scope_type, config or {})


if __name__ == "__main__":
    unittest.main()
