# SPDX-License-Identifier: Apache-2.0
"""Native archive/read/notification races; no browser, models, or OS popups."""

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.conversations import ConversationStore
from harness2.profiles import ProfileStore
from harness2.ui.attention import Attention
from harness2.ui.workspace import Workspace


class ConversationAttentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root)
        profiles = ProfileStore(self.root)
        profiles.create("Owen")
        self.configuration = profiles.add_configuration("owen", {
            "settings": {"provider": "local"}, "identity": {"model": "fixture"}})
        self.revision = profiles.add_revision("owen", {})
        self.conversations = ConversationStore(self.root)
        self.identifier = self.conversations.create("owen", self.configuration["id"], self.revision["id"])["id"]
        self.conversations.bind_worker(self.identifier, "worker", label="Fixture", session_id="ses_fixture")
        self.attention = Attention(self.workspace, self.conversations)

    def message(self, *, origin="model_initiated", text="A surprising observation"):
        return self.conversations.assistant(self.identifier, text, worker_id="worker", origin=origin)

    def decorate(self):
        return self.attention.decorate(self.conversations.get(self.identifier))

    def claim(self, message):
        return self.attention.claim({"conversation_id": self.identifier, "message_id": message["id"]})

    def history(self):
        return {str(path): path.read_bytes() for path in (self.root / ".harness2").rglob("*.json")}

    def test_reads_and_notification_preferences_do_not_change_model_records(self):
        first = self.message()
        reply = self.message(origin="reply")
        self.conversations.enqueue(self.identifier, "A human message is not unread model contact")
        before = self.history()
        self.assertEqual(self.attention.state(), {"enabled": False})
        self.assertEqual(self.decorate()["unread_count"], 2)
        self.attention.read(self.identifier, [first["id"]])
        # A new append racing a read is not consumed by the older render.
        latest = self.message(text="Another observation")
        before = self.history()
        self.attention.read(self.identifier, [first["id"]])
        self.attention.settings({"enabled": True})
        self.assertEqual(set(self.decorate()["unread_message_ids"]), {reply["id"], latest["id"]})
        self.assertEqual(self.history(), before)
        reopened = Attention(Workspace(self.root), ConversationStore(self.root))
        self.assertEqual(reopened.decorate(self.conversations.get(self.identifier)), self.decorate())
        self.assertEqual(reopened.state(), {"enabled": True})

    def test_enable_baselines_backlog_and_claim_does_not_mark_a_new_message_read(self):
        backlog = self.message()
        self.attention.settings({"enabled": True})
        self.assertFalse(self.claim(backlog)["claimed"])
        current = self.message()
        self.assertTrue(self.claim(current)["claimed"])
        self.assertFalse(self.claim(current)["claimed"])
        self.assertEqual(self.decorate()["unread_count"], 2)
        self.attention.read(self.identifier, [current["id"]])
        self.assertEqual(self.decorate()["unread_count"], 1)
        self.assertFalse(self.claim(current)["claimed"])
        self.attention.settings({"enabled": False})
        disabled = self.message()
        self.assertFalse(self.claim(disabled)["claimed"])
        self.attention.settings({"enabled": True})
        self.assertFalse(self.claim(disabled)["claimed"], "Re-enabling must not replay missed popups")

    def test_replies_and_legacy_unlinked_posts_never_generate_notification_claims(self):
        self.attention.settings({"enabled": True})
        for origin in ("reply", "legacy"):
            message = self.message(origin=origin)
            self.assertFalse(self.claim(message)["claimed"])
        user = self.conversations.enqueue(self.identifier, "My question")
        self.assertFalse(self.claim(user)["claimed"])
        self.assertEqual(self.decorate()["unread_count"], 2)

    def test_read_references_are_exact_assistant_ids_and_reject_cross_conversation(self):
        user = self.conversations.enqueue(self.identifier, "A user turn")
        for identifiers in ([user["id"]], ["f" * 32], ["bad"], "all", ["a" * 32] * 2):
            with self.subTest(identifiers=identifiers), self.assertRaises(ValueError):
                self.attention.read(self.identifier, identifiers)
        for setting in ({"enabled": "true"}, {"enabled": 1}, {"enabled": True, "extra": True}, {}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.attention.settings(setting)

    def test_atomic_claims_allow_only_one_browser_attempt_across_native_processes(self):
        self.attention.settings({"enabled": True})
        message = self.message()
        program = (
            "import sys; from pathlib import Path; from harness2.conversations import ConversationStore; "
            "from harness2.ui.attention import Attention; from harness2.ui.workspace import Workspace; "
            "a=Attention(Workspace(Path(sys.argv[1])),ConversationStore(Path(sys.argv[1]))); "
            "print(a.claim({'conversation_id':sys.argv[2],'message_id':sys.argv[3]})['claimed'])"
        )
        children = [subprocess.Popen([sys.executable, "-c", program, str(self.root), self.identifier, message["id"]],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        self.addCleanup(lambda: [child.kill() for child in children if child.poll() is None])
        answers = []
        for child in children:
            output, error = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
            answers.append(output.strip() == b"True")
        self.assertEqual(sum(answers), 1)
        self.assertEqual(self.decorate()["unread_count"], 1)

    def test_concurrent_reads_preserve_both_visible_message_sets(self):
        messages = [self.message() for _ in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda item: self.attention.read(self.identifier, [item["id"]]), messages))
        self.assertEqual(self.decorate()["unread_count"], 0)

    def test_malformed_attention_state_is_rejected_without_rewriting_model_history(self):
        message = self.message()
        before = self.history()
        with self.attention.archive._locked():
            self.attention._save({"version": True, "enabled": False, "read": {}, "attempted": {}})
        with self.assertRaises(ValueError):
            self.attention.state()
        self.assertEqual(self.history(), before)
        self.assertEqual(self.conversations.get(self.identifier)["messages"][0]["id"], message["id"])

    def test_attention_directory_cannot_be_redirected_through_a_link(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.attention.archive.root
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest("Windows junction creation unavailable")
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.addCleanup(lambda: link.rmdir() if os.name == "nt" else link.unlink())
        with self.assertRaises(ValueError):
            self.attention.state()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
