# SPDX-License-Identifier: Apache-2.0
"""Conversation persistence and native delivery lifecycle; no models are called."""

import concurrent.futures
import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.conversations import ConversationStore, CorruptConversationError
from harness2.profiles import ProfileStore


class ConversationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = ProfileStore(self.root)
        self.profiles.create("Owen")
        self.configuration = self.profiles.add_configuration("owen", {
            "settings": {"provider": "local"}, "identity": {"model": "synthetic"}})
        self.revision = self.profiles.add_revision("owen", {"guardrails.md": "Verify useful progress."})
        self.store = ConversationStore(self.root)
        self.conversation = self.create()
        self.identifier = self.conversation["id"]

    def create(self, **kwargs):
        return self.store.create("owen", self.configuration["id"], self.revision["id"], **kwargs)

    def bind(self, worker="worker-one"):
        return self.store.bind_worker(self.identifier, worker, label="Synthetic worker", session_id="session-one")

    def bind_native(self, worker="worker-one", session="ses_TestSession"):
        return self.store.bind_worker(self.identifier, worker, label="Synthetic native worker", session_id=session)

    @staticmethod
    def ancestry(message, parent="msg_UserTurn", session="ses_TestSession"):
        return {"native_session_id": session, "native_message_id": message, "native_parent_id": parent}

    def delivered_native(self, text="A question", parent="msg_UserTurn"):
        message = self.store.enqueue(self.identifier, text)
        self.store.transition(self.identifier, message["id"], "delivered", session_message_id=parent)
        return message

    def history_bytes(self):
        return {path: path.read_bytes() for path in (self.store.root / self.identifier / "history/events").glob("*.json")}

    def profile_bytes(self):
        return {path: path.read_bytes() for path in self.profiles.root.rglob("*.json")}

    def test_identity_and_empty_layers_do_not_mutate_the_technical_profile(self):
        before = self.profile_bytes()
        self.assertEqual(self.conversation["profile_id"], "owen")
        self.assertEqual(self.conversation["configuration_id"], self.configuration["id"])
        self.assertEqual(self.conversation["revision_id"], self.revision["id"])
        self.assertEqual(self.conversation["status"], "open")
        self.assertEqual(self.conversation["messages"], [])
        self.assertEqual(self.conversation["decisions"], [])
        self.assertEqual(self.conversation["recovery"], {})
        self.assertEqual(self.store.memory("owen"), [])
        self.assertEqual(self.store.list("owen")[0]["id"], self.identifier)
        self.assertEqual(self.store.list("other"), [])
        self.assertEqual(self.profile_bytes(), before)
        self.assertNotEqual(self.store.memory_root, self.profiles.root)
        with self.assertRaises(ValueError):
            self.store.create("owen", "f" * 64, self.revision["id"])
        with self.assertRaises(ValueError):
            self.store.create("owen", self.configuration["id"], "f" * 64)

    def test_queue_delivery_and_visible_reply_have_distinct_truthful_states(self):
        with self.assertRaisesRegex(ValueError, "active worker"):
            self.store.enqueue(self.identifier, "Why this approach?")
        self.bind()
        queued = self.store.enqueue(self.identifier, "Why this approach?")
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["worker_id"], "worker-one")
        with self.assertRaises(ValueError):
            self.store.enqueue(self.identifier, "Wrong target", worker_id="another-worker")
        with self.assertRaises(ValueError):
            self.store.assistant(self.identifier, "Premature answer", worker_id="worker-one", reply_to=[queued["id"]])
        self.store.transition(self.identifier, queued["id"], "delivered", session_message_id="native-user-one")
        delivered = self.store.get(self.identifier)["messages"][0]
        self.assertEqual(delivered["status"], "delivered")
        with self.assertRaises(ValueError):
            self.store.transition(self.identifier, queued["id"], "answered")
        response = self.store.assistant(self.identifier, "I will verify the sources before changing course.",
            worker_id="worker-one", session_message_id="native-assistant-one", reply_to=[queued["id"]])
        messages = self.store.get(self.identifier)["messages"]
        self.assertEqual(messages[0]["status"], "answered")
        self.assertEqual(messages[0]["answered_by"], response["id"])
        self.assertEqual(response["reply_to"], [queued["id"]])
        self.assertEqual(response["role"], "assistant")
        self.assertEqual(messages[0]["session_message_id"], "native-user-one")
        self.assertEqual(len(messages), 2)

    def test_native_assistant_ids_are_deduplicated_without_answering_newer_messages(self):
        self.bind()
        first = self.store.enqueue(self.identifier, "First question")
        self.store.transition(self.identifier, first["id"], "delivered")
        answer = self.store.assistant(self.identifier, "A completed visible response.", worker_id="worker-one",
            session_message_id="native-response", reply_to=[first["id"]])
        before = self.store.get(self.identifier)
        replay = self.store.assistant(self.identifier, answer["text"], worker_id="worker-one", session_message_id="native-response")
        self.assertEqual(replay, answer)
        self.assertEqual(self.store.get(self.identifier), before)
        second = self.store.enqueue(self.identifier, "Second question")
        self.store.transition(self.identifier, second["id"], "delivered")
        with self.assertRaises(ValueError):
            self.store.assistant(self.identifier, answer["text"], worker_id="worker-one",
                                 session_message_id="native-response", reply_to=[second["id"]])
        with self.assertRaises(ValueError):
            self.store.assistant(self.identifier, "A different response", worker_id="worker-one", session_message_id="native-response")
        self.assertEqual(self.store.get(self.identifier)["messages"][-1]["status"], "delivered")

    def test_worker_end_marks_queued_undelivered_and_delivered_unanswered_failed(self):
        self.bind()
        queued = self.store.enqueue(self.identifier, "Queued while the worker stops")
        delivered = self.store.enqueue(self.identifier, "Already sent to the worker")
        self.store.transition(self.identifier, delivered["id"], "delivered")
        ended = self.store.end_worker(self.identifier, "worker-one", "Worker finished before replying.")
        self.assertIsNone(ended["active_worker_id"])
        self.assertEqual(ended["messages"][0]["id"], queued["id"])
        self.assertEqual(ended["messages"][0]["status"], "undelivered")
        self.assertEqual(ended["messages"][1]["status"], "failed")
        self.assertEqual(ended["workers"][0]["status"], "ended")
        self.assertTrue(all(message["reason"] for message in ended["messages"]))
        self.assertEqual(self.store.end_worker(self.identifier, "worker-one", "Repeated close"), ended)
        with self.assertRaises(ValueError):
            self.store.enqueue(self.identifier, "There is no worker now")
        with self.assertRaises(ValueError):
            self.store.bind_worker(self.identifier, "worker-one", label="Synthetic worker", session_id="a-new-session")

    def test_committed_tool_message_retries_after_restart_without_new_unread_entry(self):
        self.bind()
        tool_id = "tool:" + "a" * 32
        original = self.store.assistant(self.identifier, "A useful stage update.", worker_id="worker-one",
                                        session_message_id=tool_id)
        native = self.store.assistant(self.identifier, "An ordinary native answer.", worker_id="worker-one",
                                      session_message_id="msg_native_reply")
        self.store.end_worker(self.identifier, "worker-one", "Restarted before writing the tool acknowledgement.")
        self.bind("worker-two")
        restored = ConversationStore(self.root)
        before = restored.get(self.identifier)
        replay = restored.assistant(self.identifier, original["text"], worker_id="worker-two", session_message_id=tool_id)
        self.assertEqual(replay, original)
        self.assertEqual(restored.get(self.identifier), before)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            restored.assistant(self.identifier, "An altered stage update.", worker_id="worker-two", session_message_id=tool_id)
        self.assertEqual(restored.get(self.identifier), before)
        later_native = restored.assistant(self.identifier, native["text"], worker_id="worker-two",
                                          session_message_id="msg_native_reply")
        self.assertNotEqual(later_native["id"], native["id"])
        self.assertEqual(len(restored.get(self.identifier)["messages"]), 3)

    def test_native_ancestry_projects_exact_tool_reply_alias_without_rewriting_history(self):
        self.bind_native()
        user = self.delivered_native()
        tool = self.store.assistant(self.identifier, "An exact reply.\n", worker_id="worker-one",
            session_message_id="tool:" + "a" * 32, origin="reply", **self.ancestry("msg_ToolReply"))
        raw_before = self.history_bytes()
        terminal = self.store.assistant(self.identifier, tool["text"], worker_id="worker-one",
            session_message_id="msg_TerminalReply", reply_to=[user["id"]], **self.ancestry("msg_TerminalReply"))
        projected = self.store.get(self.identifier)["messages"]
        self.assertEqual(len(projected), 3)
        self.assertEqual(projected[1]["duplicate_of"], terminal["id"])
        self.assertNotIn("duplicate_of", projected[2])
        self.assertEqual(projected[0]["answered_by"], terminal["id"])
        self.assertEqual({path: path.read_bytes() for path in raw_before}, raw_before)
        raw = [json.loads(value) for value in self.history_bytes().values()]
        self.assertEqual(len([event for event in raw if event["action"] == "assistant_added"]), 2)
        self.assertNotIn("duplicate_of", json.dumps(raw))
        self.assertEqual(ConversationStore(self.root).get(self.identifier)["messages"], projected)

    def test_native_alias_needs_exact_text_human_anchor_and_reply_origin(self):
        self.bind_native()
        user = self.delivered_native()
        visible = []
        for index, (text, origin, parent) in enumerate((
            ("A reply", "reply", "msg_OtherTurn"),
            ("A reply ", "reply", "msg_UserTurn"),
            ("A reply", "model_initiated", "msg_UserTurn"),
            ("A useful progress update", "reply", "msg_UserTurn"),
        )):
            visible.append(self.store.assistant(self.identifier, text, worker_id="worker-one",
                session_message_id="tool:" + str(index) * 32, origin=origin,
                **self.ancestry("msg_Tool" + str(index), parent=parent)))
        legacy = self.store.assistant(self.identifier, "A reply", worker_id="worker-one",
            session_message_id="tool:" + "a" * 32, **self.ancestry("msg_LegacyTool"))
        terminal = self.store.assistant(self.identifier, "A reply", worker_id="worker-one",
            session_message_id="msg_Terminal", reply_to=[user["id"]], **self.ancestry("msg_Terminal"))
        messages = {item["id"]: item for item in self.store.get(self.identifier)["messages"]}
        self.assertTrue(all("duplicate_of" not in messages[item["id"]] for item in visible))
        self.assertEqual(messages[legacy["id"]]["origin"], "legacy")
        self.assertEqual(messages[legacy["id"]]["duplicate_of"], terminal["id"])

        unanchored = self.delivered_native(parent="msg_ActualUser")
        tool = self.store.assistant(self.identifier, "An unanchored reply", worker_id="worker-one",
            session_message_id="tool:" + "b" * 32, origin="reply", **self.ancestry("msg_UnanchoredTool", parent="msg_DifferentUser"))
        self.store.assistant(self.identifier, tool["text"], worker_id="worker-one", session_message_id="msg_UnanchoredFinal",
            reply_to=[unanchored["id"]], **self.ancestry("msg_UnanchoredFinal", parent="msg_DifferentUser"))
        self.assertNotIn("duplicate_of", next(item for item in self.store.get(self.identifier)["messages"] if item["id"] == tool["id"]))

    def test_same_text_in_distinct_native_turns_workers_and_sessions_is_preserved(self):
        self.bind_native()
        early_tool = self.store.assistant(self.identifier, "Hello", worker_id="worker-one", origin="reply",
            session_message_id="tool:" + "a" * 32, **self.ancestry("msg_EarlyTool", parent="msg_EarlyUser"))
        later_user = self.delivered_native(parent="msg_LaterUser")
        self.store.assistant(self.identifier, "Hello", worker_id="worker-one", session_message_id="msg_LaterReply",
            reply_to=[later_user["id"]], **self.ancestry("msg_LaterReply", parent="msg_LaterUser"))
        self.store.end_worker(self.identifier, "worker-one", "Restarted")
        self.bind_native("worker-two")
        resumed_user = self.delivered_native(parent="msg_EarlyUser")
        self.store.assistant(self.identifier, "Hello", worker_id="worker-two", session_message_id="msg_ResumedReply",
            reply_to=[resumed_user["id"]], **self.ancestry("msg_ResumedReply", parent="msg_EarlyUser"))
        self.store.end_worker(self.identifier, "worker-two", "Another session")
        self.bind_native("worker-three", session="ses_AnotherSession")
        another_user = self.delivered_native(parent="msg_EarlyUser")
        self.store.assistant(self.identifier, "Hello", worker_id="worker-three", session_message_id="msg_NewSessionReply",
            reply_to=[another_user["id"]], **self.ancestry("msg_NewSessionReply", parent="msg_EarlyUser", session="ses_AnotherSession"))
        messages = self.store.get(self.identifier)["messages"]
        self.assertTrue(all("duplicate_of" not in item for item in messages))
        self.assertEqual(next(item for item in messages if item["id"] == early_tool["id"])["text"], "Hello")
        self.assertEqual(len([item for item in messages if item["role"] == "assistant"]), 4)

    def test_native_link_enrichment_is_append_only_idempotent_and_works_after_close(self):
        self.bind_native()
        user = self.delivered_native()
        tool = self.store.assistant(self.identifier, "A retained reply", worker_id="worker-one",
            session_message_id="tool:" + "a" * 32, origin="reply")
        terminal = self.store.assistant(self.identifier, tool["text"], worker_id="worker-one",
            session_message_id="msg_RetainedFinal", reply_to=[user["id"]])
        self.store.finish(self.identifier, "Finished before native ancestry was recovered")
        raw_before = self.history_bytes()
        self.store.link_native_message(self.identifier, terminal["id"], **self.ancestry("msg_RetainedFinal"))
        linked = self.store.link_native_message(self.identifier, tool["id"], **self.ancestry("msg_RetainedTool"))
        self.assertEqual(linked["duplicate_of"], terminal["id"])
        self.assertEqual({path: path.read_bytes() for path in raw_before}, raw_before)
        raw_after = self.history_bytes()
        self.assertEqual(len(raw_after), len(raw_before) + 2)
        additions = [json.loads(value) for path, value in raw_after.items() if path not in raw_before]
        self.assertTrue(all(event["action"] == "assistant_native_linked" for event in additions))
        self.assertEqual(self.store.link_native_message(self.identifier, tool["id"], **self.ancestry("msg_RetainedTool")), linked)
        self.assertEqual(self.history_bytes(), raw_after)
        for changes in ({"native_parent_id": "msg_ChangedParent"}, {"native_message_id": "msg_ChangedMessage"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "cannot change"):
                self.store.link_native_message(self.identifier, tool["id"], **(self.ancestry("msg_RetainedTool") | changes))
        self.assertEqual(self.history_bytes(), raw_after)

    def test_native_metadata_rejects_partial_malformed_conflicting_and_cross_session_links(self):
        self.bind_native()
        user = self.delivered_native()
        tool = self.store.assistant(self.identifier, "A tool update", worker_id="worker-one", session_message_id="tool:" + "a" * 32)
        before = self.history_bytes()
        invalid = (
            {"native_session_id": "ses_TestSession"},
            self.ancestry("msg_bad_id"),
            self.ancestry("msg_Tool", session="ses_bad_id"),
            self.ancestry("msg_Tool", parent="msg_Tool"),
            self.ancestry("msg_Tool", session="ses_AnotherSession"),
            self.ancestry("msg_" + "a" * 129),
            self.ancestry("msg_Tool", parent="../outside"),
        )
        for native in invalid:
            with self.subTest(native=native), self.assertRaises(ValueError):
                self.store.assistant(self.identifier, "Rejected", worker_id="worker-one", **native)
        with self.assertRaisesRegex(ValueError, "transport"):
            self.store.assistant(self.identifier, "Rejected", worker_id="worker-one",
                session_message_id="msg_Different", **self.ancestry("msg_Tool"))
        with self.assertRaisesRegex(ValueError, "Only assistant"):
            self.store.link_native_message(self.identifier, user["id"], **self.ancestry("msg_Tool"))
        with self.assertRaisesRegex(ValueError, "worker's session"):
            self.store.link_native_message(self.identifier, tool["id"], **self.ancestry("msg_Tool", session="ses_AnotherSession"))
        self.assertEqual(self.history_bytes(), before)

    def test_native_message_replays_are_stable_even_after_worker_restart(self):
        self.bind_native()
        user = self.delivered_native()
        packet = {"session_message_id": "msg_Final", "reply_to": [user["id"]], **self.ancestry("msg_Final")}
        first = self.store.assistant(self.identifier, "A committed final reply", worker_id="worker-one", **packet)
        before = self.history_bytes()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.store.assistant(self.identifier, first["text"], worker_id="worker-one", **packet), range(4)))
        self.assertTrue(all(reply == first for reply in replies))
        self.assertEqual(self.history_bytes(), before)
        self.store.end_worker(self.identifier, "worker-one", "Restarted")
        self.bind_native("worker-two")
        before = self.history_bytes()
        replay = self.store.assistant(self.identifier, first["text"], worker_id="worker-two",
            **{key: value for key, value in packet.items() if key != "reply_to"})
        self.assertEqual(replay, first)
        self.assertEqual(self.history_bytes(), before)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            self.store.assistant(self.identifier, first["text"], worker_id="worker-two",
                **(packet | {"native_parent_id": "msg_ChangedParent"}))

    def test_journal_replay_rejects_unvalidated_native_ancestry(self):
        self.bind_native()
        tool = self.store.assistant(self.identifier, "A tool update", worker_id="worker-one", session_message_id="tool:" + "a" * 32)
        with self.store._archive._locked():
            _, history, _ = self.store._state(self.identifier)
            self.store._archive._append(self.store.root / self.identifier / "history", history, "assistant_native_linked", {
                "message_id": tool["id"], "native_session_id": "ses_TestSession"})
        with self.assertRaises(CorruptConversationError):
            self.store.get(self.identifier)

    def test_committed_decision_request_retries_across_workers_and_rejects_changes(self):
        self.bind()
        source = self.store.enqueue(self.identifier, "Please preserve the consequence of this exchange.")
        packet = {"text": "Preserve decision ancestry.", "why": "The exchange changed the plan.",
                  "source_message_ids": [source["id"]], "status": "adopted", "request_id": "b" * 32}
        first = self.store.record_decision(self.identifier, worker_id="worker-one", **packet)
        revised = self.store.record_decision(self.identifier, text="Keep a narrower trace.", why="A later refinement.",
            source_message_ids=[source["id"]], status="revised", supersedes_id=first["id"], worker_id="worker-one")
        self.store.end_worker(self.identifier, "worker-one", "Restarted before acknowledgement.")
        self.bind("worker-two")
        restored = ConversationStore(self.root)
        before = restored.get(self.identifier)
        replay = restored.record_decision(self.identifier, worker_id="worker-two", **packet)
        self.assertEqual(replay, first)
        self.assertEqual(restored.get(self.identifier), before)
        self.assertEqual(before["decisions"][-1], revised)
        for changes in ({"text": "Different choice."}, {"why": "Different ancestry."}, {"status": "proposed"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "cannot change"):
                restored.record_decision(self.identifier, worker_id="worker-two", **(packet | changes))
        with self.assertRaisesRegex(ValueError, "cannot change"):
            restored.checkpoint(self.identifier, objective="A request cannot change kind.", request_id=packet["request_id"])
        self.assertEqual(restored.get(self.identifier), before)

    def test_old_checkpoint_retry_returns_original_without_replacing_later_recovery(self):
        self.bind()
        packet = {"objective": "Write the deliverable.", "completed": ["Read the task."],
                  "planned": ["Write the output."], "open_questions": ["Which details need citations?"],
                  "request_id": "c" * 32}
        first = self.store.checkpoint(self.identifier, worker_id="worker-one", **packet)
        latest = self.store.checkpoint(self.identifier, worker_id="worker-one", objective="Verify the deliverable.",
            completed=["Wrote the output."], planned=["Check citations."], request_id="d" * 32)
        self.store.end_worker(self.identifier, "worker-one", "Restarted before an old acknowledgement.")
        self.bind("worker-two")
        restored = ConversationStore(self.root)
        before = restored.get(self.identifier)
        replay = restored.checkpoint(self.identifier, worker_id="worker-two", **packet)
        self.assertEqual(replay, first)
        self.assertEqual(restored.get(self.identifier), before)
        self.assertEqual(restored.get(self.identifier)["recovery"], latest)
        for changes in ({"planned": ["A different next step."]}, {"completed": ["Claim a different result."]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "cannot change"):
                restored.checkpoint(self.identifier, worker_id="worker-two", **(packet | changes))
        self.assertEqual(restored.get(self.identifier), before)
        for identifier in ("", "A" * 32, "x" * 32, "a" * 64):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                restored.checkpoint(self.identifier, request_id=identifier)

    def test_new_worker_does_not_inherit_predecessor_pending_messages(self):
        self.bind()
        prior = self.store.enqueue(self.identifier, "For the earlier worker")
        bound = self.bind("worker-two")
        self.assertEqual(bound["active_worker_id"], "worker-two")
        self.assertEqual(bound["messages"][0]["status"], "undelivered")
        self.assertEqual(bound["messages"][0]["worker_id"], "worker-one")
        new = self.store.enqueue(self.identifier, "For the new worker")
        self.assertEqual(new["worker_id"], "worker-two")
        with self.assertRaises(ValueError):
            self.store.assistant(self.identifier, "Wrong worker", worker_id="worker-two", reply_to=[prior["id"]])

    def test_finish_preserves_transcript_and_expires_all_pending_messages(self):
        self.bind()
        self.store.enqueue(self.identifier, "An unsent thought")
        delivered = self.store.enqueue(self.identifier, "A sent thought")
        self.store.transition(self.identifier, delivered["id"], "delivered")
        finished = self.store.finish(self.identifier, "The conversation was closed.")
        self.assertEqual(finished["status"], "closed")
        self.assertEqual([message["status"] for message in finished["messages"]], ["undelivered", "failed"])
        self.assertIsNone(finished["active_worker_id"])
        self.assertEqual(self.store.finish(self.identifier, "Repeated close"), finished)
        for call in (lambda: self.store.enqueue(self.identifier, "Late message"),
                     lambda: self.bind("another"),
                     lambda: self.store.assistant(self.identifier, "Late new turn", worker_id="worker-one")):
            with self.assertRaises(ValueError):
                call()

    def test_failure_transitions_require_reasons_and_cannot_relabel_completed_messages(self):
        self.bind()
        first = self.store.enqueue(self.identifier, "First")
        for status in ("failed", "undelivered"):
            with self.assertRaises(ValueError):
                self.store.transition(self.identifier, first["id"], status)
        self.store.transition(self.identifier, first["id"], "undelivered", reason="No acknowledgement received.")
        with self.assertRaises(ValueError):
            self.store.transition(self.identifier, first["id"], "delivered")
        second = self.store.enqueue(self.identifier, "Second")
        self.store.transition(self.identifier, second["id"], "delivered", session_message_id="native-two")
        with self.assertRaises(ValueError):
            self.store.transition(self.identifier, second["id"], "undelivered", reason="Wrong phase")
        with self.assertRaises(ValueError):
            self.store.transition(self.identifier, second["id"], "delivered", session_message_id="different-native")
        self.store.transition(self.identifier, second["id"], "failed", reason="The worker never completed an answer.")

    def test_decision_ancestry_is_sourced_append_only_and_separate_from_transcript(self):
        before = self.profile_bytes()
        self.bind()
        source = self.store.enqueue(self.identifier, "VISIBLE_BANTER_SENTINEL Why not write the files earlier?")
        original = self.store.record_decision(self.identifier, text="Consider writing files earlier.",
            why="A conversation raised the possibility.", source_message_ids=[source["id"]], worker_id="worker-one")
        adopted = self.store.record_decision(self.identifier, text="Write a first draft before more research.",
            why="This gives us a concrete artifact to inspect.", source_message_ids=[source["id"]],
            status="adopted", supersedes_id=original["id"], worker_id="worker-one")
        reverted = self.store.record_decision(self.identifier, text="Return to source verification first.",
            why="The available sources conflict.", source_message_ids=[source["id"]],
            status="reverted", supersedes_id=adopted["id"])
        decisions = self.store.get(self.identifier)["decisions"]
        self.assertEqual([item["status"] for item in decisions], ["proposed", "adopted", "reverted"])
        self.assertEqual(decisions[0], original)
        self.assertEqual(decisions[-1], reverted)
        self.assertEqual(reverted["supersedes_id"], adopted["id"])
        self.assertEqual(self.profile_bytes(), before)
        self.assertEqual(self.store.memory("owen"), [])
        history = self.store.root / self.identifier / "history/events"
        working = self.store.root / self.identifier / "working/events"
        self.assertTrue(history.is_dir() and working.is_dir())
        self.assertIn("VISIBLE_BANTER_SENTINEL", "".join(path.read_text() for path in history.glob("*.json")))
        self.assertNotIn("VISIBLE_BANTER_SENTINEL", "".join(path.read_text() for path in working.glob("*.json")))
        for options in ({"source_message_ids": []}, {"source_message_ids": ["f" * 32]},
                        {"source_message_ids": [source["id"]], "status": "revised"},
                        {"source_message_ids": [source["id"]], "supersedes_id": "f" * 32}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.store.record_decision(self.identifier, text="A change", why="A reason", **options)

    def test_recovery_separates_reported_completed_and_planned_without_importing_chat(self):
        self.bind()
        source = self.store.enqueue(self.identifier, "DO_NOT_COPY_THE_TRANSCRIPT")
        decision = self.store.record_decision(self.identifier, text="Keep progress separately.",
            why="A recovery point needs explicit state.", source_message_ids=[source["id"]])
        checkpoint = self.store.checkpoint(self.identifier, objective="Produce four documents.",
            constraints=["Use only the selected source materials."],
            completed=["A first draft exists.", {"text": "A file was inspected.", "references": [{"path": "outputs/draft.md"}]}],
            planned=["Verify terminology."], open_questions=["Which source version is authoritative?"],
            decision_ids=[decision["id"]], worker_id="worker-one")
        self.assertEqual(self.store.get(self.identifier)["recovery"], checkpoint)
        self.assertEqual(checkpoint["completed"][0]["references"], [])
        self.assertEqual(checkpoint["planned"], ["Verify terminology."])
        self.assertIn("model-reported", checkpoint["completion_qualification"])
        self.assertIn("not independently verified", checkpoint["completion_qualification"])
        self.assertNotIn("DO_NOT_COPY_THE_TRANSCRIPT", json.dumps(checkpoint))
        with self.assertRaises(ValueError):
            self.store.checkpoint(self.identifier, decision_ids=["f" * 32])
        with self.assertRaises(ValueError):
            self.store.checkpoint(self.identifier, constraints=["x" * 4_096] * 16)
        event = self.store.record_event(self.identifier, "compaction_completed", {"summary_present": True}, worker_id="worker-one")
        self.assertEqual(self.store.get(self.identifier)["observations"][-1], event)

    def test_conversational_memory_requires_explicit_sources_and_never_changes_profile(self):
        before = self.profile_bytes()
        self.bind()
        source = self.store.enqueue(self.identifier, "An explicitly saved preference")
        self.assertEqual(self.store.memory("owen"), [])
        memory = self.store.add_memory("owen", text="Keep explanations concise when giving live status.",
            why="The user explicitly requested this preference.", conversation_id=self.identifier,
            source_message_ids=[source["id"]])
        self.assertEqual(self.store.memory("owen"), [memory])
        self.assertEqual(memory["source_message_ids"], [source["id"]])
        self.assertEqual(self.profile_bytes(), before)
        self.assertEqual(self.profiles.get("owen")["notes"], [])
        for sources in ([], ["f" * 32]):
            with self.assertRaises(ValueError):
                self.store.add_memory("owen", text="An unsupported note", why="No source", conversation_id=self.identifier, source_message_ids=sources)
        self.profiles.create("Another")
        with self.assertRaises(ValueError):
            self.store.add_memory("another", text="Wrong identity", why="Not theirs", conversation_id=self.identifier, source_message_ids=[source["id"]])

    def test_interrupted_append_leaves_prior_records_intact(self):
        self.bind()
        before = self.store.get(self.identifier)
        records = {path: path.read_bytes() for path in self.store.root.rglob("*.json")}
        with patch.object(self.store._archive, "_append", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.store.enqueue(self.identifier, "Not published")
        self.assertEqual(self.store.get(self.identifier), before)
        self.assertEqual({path: path.read_bytes() for path in self.store.root.rglob("*.json")}, records)
        self.store.enqueue(self.identifier, "A retry can succeed")

    @unittest.skipUnless(os.name == "nt", "Native Windows sharing retry.")
    def test_creation_retries_one_transient_windows_directory_sharing_failure(self):
        from harness2 import locking

        original = locking.os.replace
        attempts = []

        def replace(source, destination):
            if Path(source).is_dir() and Path(source).name.startswith(".create-"):
                attempts.append((Path(source), Path(destination)))
                if len(attempts) == 1:
                    failure = PermissionError(errno.EACCES, "Synthetic transient directory sharing conflict")
                    failure.winerror = 5
                    raise failure
            return original(source, destination)

        with patch.object(locking.os, "replace", side_effect=replace):
            created = self.create(title="Published after one sharing conflict")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(created["title"], "Published after one sharing conflict")
        self.assertEqual(self.store.get(created["id"]), created)
        self.assertTrue(attempts[0][1].is_relative_to(self.store.root))

    def test_creation_never_replaces_an_existing_conversation_if_an_id_collides(self):
        before = {path: path.read_bytes() for path in self.store.root.rglob("*.json")}
        with patch("harness2.conversations.uuid.uuid4", return_value=SimpleNamespace(hex=self.identifier)):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.create(title="Must not replace the original")
        self.assertEqual({path: path.read_bytes() for path in self.store.root.rglob("*.json")}, before)
        self.assertEqual(self.store.get(self.identifier), self.conversation)

    def test_corrupt_history_and_missing_working_event_are_detected(self):
        self.bind()
        message = self.store.enqueue(self.identifier, "A message")
        events = sorted((self.store.root / self.identifier / "history/events").glob("*.json"))
        raw = events[-1].read_bytes()
        value = json.loads(raw)
        value["data"]["text"] = "Altered"
        events[-1].write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(CorruptConversationError):
            self.store.get(self.identifier)
        events[-1].write_bytes(raw)
        self.store.record_decision(self.identifier, text="Preserve state", why="A stated reason", source_message_ids=[message["id"]])
        self.store.checkpoint(self.identifier, objective="A first checkpoint")
        self.store.checkpoint(self.identifier, objective="A later checkpoint")
        sorted((self.store.root / self.identifier / "working/events").glob("*.json"))[1].unlink()
        with self.assertRaises(CorruptConversationError):
            self.store.get(self.identifier)

    def test_message_limits_and_bad_identifiers_reject_without_append(self):
        self.bind()
        before = self.store.get(self.identifier)
        for text in ("", " \n ", "x\0y", "x" * 65_537):
            with self.assertRaises(ValueError):
                self.store.enqueue(self.identifier, text)
        for identifier in ("../outside", "UPPER", "a" * 64, "c:/outside"):
            with self.assertRaises(ValueError):
                self.store.get(identifier)
        self.assertEqual(self.store.get(self.identifier), before)
        with patch("harness2.conversations.MAX_MESSAGES", 1):
            self.store.enqueue(self.identifier, "One message")
            with self.assertRaises(ValueError):
                self.store.enqueue(self.identifier, "An excess message")

    def test_concurrent_threads_and_native_processes_preserve_every_queued_message(self):
        self.bind()
        def write(index):
            ConversationStore(self.root).enqueue(self.identifier, f"Thread {index}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(8)))
        script = (
            "from harness2.conversations import ConversationStore; import sys; "
            "s=ConversationStore(sys.argv[1]); "
            "[s.enqueue(sys.argv[2],'Process '+sys.argv[3]+' / '+str(i)) for i in range(3)]"
        )
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.root), self.identifier, str(i)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
        self.addCleanup(lambda: [child.kill() for child in children if child.poll() is None])
        for child in children:
            _, error = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
        view = self.store.get(self.identifier)
        self.assertEqual(len(view["messages"]), 17)
        self.assertEqual(len({message["id"] for message in view["messages"]}), 17)
        self.assertEqual(len({message["text"] for message in view["messages"]}), 17)
        self.assertTrue(all(message["status"] == "queued" for message in view["messages"]))

    def test_symlinks_or_windows_junctions_cannot_redirect_working_store(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.store.root / self.identifier / "working"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False)
            if result.returncode:
                self.skipTest("Windows junction creation unavailable.")
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.checkpoint(self.identifier, objective="Must not escape")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
