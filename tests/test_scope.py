import unittest

from scope import MissingScopeIdentityError, build_scope_from_event, build_scopes_from_event


class FakeEvent:
    def __init__(self, platform="telegram", sender="alice", group=None, umo="telegram:private:alice"):
        self.message_str = "hello"
        self.unified_msg_origin = umo
        self._platform = platform
        self._sender = sender
        self._group = group

    def get_platform_name(self):
        return self._platform

    def get_sender_id(self):
        return self._sender

    def get_group_id(self):
        return self._group


class ScopeTests(unittest.TestCase):
    def test_private_scope_hashes_sender_and_umo(self):
        scope = build_scope_from_event(FakeEvent(), "salt")

        self.assertEqual(scope.scope_type, "private")
        self.assertTrue(scope.container_tag.startswith("astrbot_private_telegram_"))
        self.assertLessEqual(len(scope.container_tag), 100)
        self.assertRegex(scope.container_tag, r"^[A-Za-z0-9_:-]+$")
        self.assertNotIn("alice", scope.container_tag)
        self.assertNotIn("alice", str(scope.metadata))

    def test_group_scope_uses_current_member_as_primary_scope(self):
        scope = build_scope_from_event(
            FakeEvent(group="group-1", umo="telegram:group:group-1"), "salt"
        )

        self.assertEqual(scope.scope_type, "group_member")
        self.assertTrue(scope.container_tag.startswith("astrbot_group_member_telegram_"))
        self.assertNotIn("group-1", scope.container_tag)
        self.assertNotIn("alice", scope.container_tag)
        self.assertNotIn("group-1", str(scope.metadata))
        self.assertEqual(scope.metadata["scope"], "group_member")

    def test_group_scopes_include_shared_and_member_layers(self):
        scopes = build_scopes_from_event(
            FakeEvent(group="group-1", umo="telegram:group:group-1"), "salt"
        )

        self.assertEqual(scopes.primary.scope_type, "group_member")
        self.assertEqual([scope.scope_type for scope in scopes.recall_scopes], ["group_shared", "group_member"])
        self.assertEqual([scope.scope_type for scope in scopes.retain_scopes], ["group_shared", "group_member"])
        self.assertTrue(scopes.recall_scopes[0].container_tag.startswith("astrbot_group_shared_telegram_"))
        self.assertTrue(scopes.recall_scopes[1].container_tag.startswith("astrbot_group_member_telegram_"))
        self.assertNotEqual(scopes.recall_scopes[0].container_tag, scopes.recall_scopes[1].container_tag)
        self.assertNotIn("group-1", scopes.recall_scopes[0].container_tag)
        self.assertNotIn("alice", scopes.recall_scopes[0].container_tag)
        self.assertEqual(scopes.recall_scopes[0].metadata["scope"], "group_shared")

    def test_group_members_have_separate_personal_container_and_shared_public_container(self):
        alice_scopes = build_scopes_from_event(
            FakeEvent(sender="alice", group="group-1", umo="telegram:group:group-1"), "salt"
        )
        bob_scopes = build_scopes_from_event(
            FakeEvent(sender="bob", group="group-1", umo="telegram:group:group-1"), "salt"
        )

        self.assertNotEqual(alice_scopes.primary.container_tag, bob_scopes.primary.container_tag)
        self.assertEqual(alice_scopes.recall_scopes[0].container_tag, bob_scopes.recall_scopes[0].container_tag)

    def test_group_scope_requires_sender_id(self):
        event = FakeEvent(sender="", group="group-1", umo="telegram:group:group-1")

        with self.assertRaises(MissingScopeIdentityError):
            build_scopes_from_event(event, "salt")

    def test_private_scope_can_use_umo_when_sender_is_missing(self):
        scope = build_scope_from_event(FakeEvent(sender="", umo="telegram:private:alice"), "salt")

        self.assertEqual(scope.scope_type, "private")

    def test_private_scope_requires_sender_or_umo(self):
        event = FakeEvent(sender="", group=None, umo="")

        with self.assertRaises(MissingScopeIdentityError):
            build_scope_from_event(event, "salt")

    def test_private_and_group_scope_are_different(self):
        private = build_scope_from_event(FakeEvent(), "salt")
        group = build_scope_from_event(FakeEvent(group="group-1"), "salt")

        self.assertNotEqual(private.container_tag, group.container_tag)

    def test_missing_group_id_falls_back_to_private(self):
        scope = build_scope_from_event(FakeEvent(group=""), "salt")

        self.assertEqual(scope.scope_type, "private")

    def test_platform_isolation_changes_scope_key(self):
        one = build_scope_from_event(FakeEvent(platform="telegram"), "salt")
        two = build_scope_from_event(FakeEvent(platform="discord"), "salt")

        self.assertNotEqual(one.scope_key, two.scope_key)

    def test_platform_is_sanitized(self):
        scope = build_scope_from_event(FakeEvent(platform="qq official/webhook"), "salt")

        self.assertRegex(scope.container_tag, r"^[A-Za-z0-9_:-]+$")
        self.assertIn("qq_official_webhook", scope.container_tag)


if __name__ == "__main__":
    unittest.main()
