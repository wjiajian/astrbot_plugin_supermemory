import re
import unittest

from scope import build_scope_from_event


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

    def test_group_scope_has_member_and_public_containers(self):
        scope = build_scope_from_event(
            FakeEvent(group="group-1", umo="telegram:group:group-1"), "salt"
        )

        self.assertEqual(scope.scope_type, "group")
        self.assertTrue(scope.container_tag.startswith("astrbot_group_member_telegram_"))
        self.assertTrue(scope.group_container_tag.startswith("astrbot_group_shared_telegram_"))
        self.assertNotEqual(scope.container_tag, scope.group_container_tag)
        self.assertNotIn("group-1", scope.container_tag)
        self.assertNotIn("group-1", scope.group_container_tag)
        self.assertNotIn("alice", scope.container_tag)
        self.assertNotIn("alice", scope.group_container_tag)
        self.assertNotIn("group-1", str(scope.metadata))
        self.assertEqual(scope.metadata["scope"], "group_member")
        self.assertEqual(scope.group_metadata["scope"], "group_shared")

    def test_group_members_have_separate_personal_container_and_shared_public_container(self):
        alice = build_scope_from_event(
            FakeEvent(sender="alice", group="group-1", umo="telegram:group:group-1"), "salt"
        )
        bob = build_scope_from_event(
            FakeEvent(sender="bob", group="group-1", umo="telegram:group:group-1"), "salt"
        )

        self.assertNotEqual(alice.container_tag, bob.container_tag)
        self.assertEqual(alice.group_container_tag, bob.group_container_tag)

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

        self.assertTrue(re.match(r"^[A-Za-z0-9_:-]+$", scope.container_tag))
        self.assertIn("qq_official_webhook", scope.container_tag)


if __name__ == "__main__":
    unittest.main()
