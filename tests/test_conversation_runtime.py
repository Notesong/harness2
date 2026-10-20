# SPDX-License-Identifier: Apache-2.0
"""Real archives with a substituted native client; no model inference here."""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from harness2 import conversation_runtime as runtime
from harness2.conversations import ConversationStore
from harness2.profiles import ProfileStore


class ConversationRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        profiles = ProfileStore(self.root)
        profiles.create("Owen")
        configuration = profiles.add_configuration("owen", {"settings": {"provider": "local"},
                                                            "identity": {"model": "synthetic"}})
        revision = profiles.add_revision("owen", {"guardrails.md": "Write useful progress."})
        self.store = ConversationStore(self.root)
        self.identifier = self.store.create("owen", configuration["id"], revision["id"])["id"]
        self.bridge = runtime.SessionBridge(self.root, self.identifier, self.root / "worker", {},
                                            model="local/test", label="Synthetic working session")
        self.bridge.client = Mock(session_id="ses_fixture")
        self.bridge.client.send.return_value = "msg_user"
        self.store.bind_worker(self.identifier, self.bridge.worker_id,
                               label="Synthetic working session", session_id="ses_fixture")
        self.bridge._recovery()
        self.addCleanup(self.bridge.close)

    def enqueue(self, text="Why this choice?"):
        return self.store.enqueue(self.identifier, text)

    def native(self, identifier, *, parent="msg_user", text="A visible answer.", **info):
        return {"info": {"id": identifier, "role": "assistant", "parentID": parent,
                         "time": {"completed": 2}, "finish": "stop", **info},
                "parts": [{"type": "text", "text": text}]}

    def request(self, **body):
        identifier = uuid.uuid4().hex
        path = self.bridge.outbox / "requests" / (identifier + ".json")
        runtime.write_json(path, {"request_id": identifier, "session_id": "ses_fixture", **body})
        self.bridge._tool_requests()
        return runtime.read_json(self.bridge.outbox / "replies" / path.name)

    def test_delivery_is_serial_and_native_response_is_matched_once(self):
        first = self.enqueue()
        second = self.enqueue("And the next step?")
        self.bridge._deliver()
        self.bridge._deliver()
        self.bridge.client.send.assert_called_once()
        messages = self.store.get(self.identifier)["messages"]
        self.assertEqual([item["status"] for item in messages], ["delivered", "queued"])
        self.assertIn(first["id"], self.bridge.client.send.call_args.args[0])
        response = self.native("msg_answer")
        # Multiple assistant steps share the same native parent. Only one may
        # claim this visible answer; later work must not re-answer it.
        self.bridge._answers([response, self.native("msg_other", text="Continuing work.")])
        self.bridge._answers([response])
        messages = self.store.get(self.identifier)["messages"]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["status"], "answered")
        self.assertEqual(messages[-1]["reply_to"], [first["id"]])
        self.assertEqual(messages[1]["id"], second["id"])
        self.assertEqual(runtime.provenance(self.store, self.identifier)["human_messages_delivered"], 1)

    def test_ambiguous_receipt_is_terminal_and_never_automatically_resent(self):
        self.enqueue()
        self.bridge.client.send.side_effect = OSError("Accepted but receipt unknown")
        self.bridge._deliver()
        self.bridge._deliver()
        self.bridge.client.send.assert_called_once()
        message = self.store.get(self.identifier)["messages"][0]
        self.assertEqual(message["status"], "undelivered")
        self.assertIn("may have reached", message["reason"])
        self.assertFalse(runtime.provenance(self.store, self.identifier)["human_assisted"])
        self.assertTrue(runtime.provenance(self.store, self.identifier)["human_contact_possible"])

    def test_original_work_prompt_precedes_human_delivery(self):
        self.bridge._wait_for_initial = True
        self.enqueue()
        self.bridge._deliver()
        self.bridge.client.send.assert_not_called()
        self.bridge._answers([{"info": {"id": "msg_mission", "role": "user"}, "parts": []}])
        self.bridge._deliver()
        self.bridge.client.send.assert_called_once()

    def test_error_has_terminal_state_and_compaction_is_not_a_human_answer(self):
        self.enqueue()
        self.bridge._deliver()
        self.bridge._answers([self.native("msg_summary", summary=True, text="Compressed working state.")])
        self.assertEqual(self.store.get(self.identifier)["messages"][0]["status"], "delivered")
        failure = self.native("msg_failed", text="", has_error=True)
        self.bridge._answers([failure, failure])
        self.bridge._answers([failure])
        self.assertEqual(self.store.get(self.identifier)["messages"][0]["status"], "failed")

    def test_tool_call_steps_do_not_consume_the_eventual_human_answer(self):
        question = self.enqueue()
        self.bridge._deliver()
        step = self.native("msg_in_progress", finish="tool-calls", text="Let me inspect that first.")
        self.bridge._answers([step])
        self.assertEqual(self.store.get(self.identifier)["messages"][0]["status"], "delivered")
        final = self.native("msg_final", text="The inspected source explains this choice.")
        self.bridge._answers([step, final])
        messages = self.store.get(self.identifier)["messages"]
        self.assertEqual(messages[0]["status"], "answered")
        self.assertEqual(messages[1]["text"], final["parts"][0]["text"])
        self.assertEqual(messages[1]["reply_to"], [question["id"]])

    def test_compaction_observations_only_count_actual_resumed_tool_identities(self):
        old = self.native("msg_old", parent="task", text="")
        old["parts"] = [{"id": "part_old", "type": "tool", "tool": "read"}]
        summary = self.native("msg_summary", parent="task", summary=True, text="Retain objective and unanswered question.")
        new = self.native("msg_new", parent="task", text="")
        new["parts"] = [{"id": "part_new", "type": "tool", "tool": "write"}]
        self.bridge._answers([old, summary, new])
        self.bridge._answers([old, summary, new])
        observations = self.store.get(self.identifier)["observations"]
        self.assertEqual([item["kind"] for item in observations], ["compaction_summary", "resumed_action"])
        self.assertEqual(observations[-1]["detail"]["part_id"], "part_new")

    def test_continuation_does_not_recount_retained_compactions_or_old_resumed_tools(self):
        summary = self.native("msg_old_summary", parent="task", summary=True, text="Old working summary.")
        old = self.native("msg_old_action", parent="task", text="")
        old["parts"] = [{"id": "part_old_action", "type": "tool", "tool": "read"}]
        self.bridge._answers([summary, old])
        before = self.store.get(self.identifier)["observations"]
        continued = runtime.SessionBridge(self.root, self.identifier, self.root / "continued", {},
                                          model="local/test", label="Continued session",
                                          resume_session_id="ses_fixture")
        continued.client = Mock(session_id="ses_fixture")
        self.store.bind_worker(self.identifier, continued.worker_id,
                               label="Continued session", session_id="ses_fixture")
        self.addCleanup(continued.close)
        continued._answers([summary, old])
        self.assertEqual(self.store.get(self.identifier)["observations"], before)
        fresh = self.native("msg_new_summary", parent="task", summary=True, text="A new boundary.")
        resumed = self.native("msg_new_action", parent="task", text="")
        resumed["parts"] = [{"id": "part_new_action", "type": "tool", "tool": "write"}]
        continued._answers([summary, old, fresh, resumed])
        continued._answers([summary, old, fresh, resumed])
        added = self.store.get(self.identifier)["observations"][len(before):]
        self.assertEqual([item["kind"] for item in added], ["compaction_summary", "resumed_action"])
        self.assertEqual(added[0]["detail"]["message_id"], "msg_new_summary")
        self.assertEqual(added[1]["detail"]["part_id"], "part_new_action")

    def test_tools_acknowledge_real_sourced_ledger_records_without_promoting_chat(self):
        source = self.enqueue("Write deliverables earlier.")
        proactive = self.request(kind="message", text="I found an uncertain source.")
        self.assertTrue(proactive["ok"])
        decision = self.request(kind="decision", text="Write a draft first.", why="The user identified a delay.",
                                source_message_ids=[source["id"]], status="adopted")
        self.assertTrue(decision["ok"])
        checkpoint = self.request(kind="checkpoint", objective="Finish documentation", completed=["Inspected source"],
                                  planned=["Write draft"], open_questions=["Which source is current?"],
                                  decision_ids=[decision["record"]["id"]])
        self.assertTrue(checkpoint["ok"])
        self.bridge._tool_requests()
        view = self.store.get(self.identifier)
        self.assertEqual(len(view["decisions"]), 1)
        self.assertEqual(len(view["messages"]), 2)
        self.assertEqual(self.store.memory("owen"), [])
        recovery = runtime.read_json(self.bridge.recovery_file)
        self.assertEqual(recovery["checkpoint"]["planned"], ["Write draft"])
        self.assertNotIn("messages", recovery)
        self.assertTrue(self.request(kind="compaction")["ok"])
        wrong = self.request(kind="message", text="Wrong origin", session_id="ses_other")
        self.assertFalse(wrong["ok"])
        self.assertEqual(len(self.store.get(self.identifier)["messages"]), 2)

    def test_recovery_does_not_silently_drop_the_earliest_decisions(self):
        source = self.enqueue()
        for number in range(25):
            self.store.record_decision(self.identifier, text=f"Decision {number}", why="Reason " + "a" * 500,
                                       source_message_ids=[source["id"]])
        self.bridge._recovery()
        self.assertEqual(len(runtime.read_json(self.bridge.recovery_file)["decisions"]), 25)
        before = self.store.get(self.identifier)
        # The store permits a bounded decision, but the complete recovery view
        # must fit too. Rejection occurs before committing it.
        self.assertFalse(self.request(kind="decision", text="x" * 8000, why="y" * 8000,
                                      source_message_ids=[source["id"]])["ok"])
        self.assertEqual(self.store.get(self.identifier)["decisions"], before["decisions"])

    def test_close_preserves_pending_delivery_distinctions_and_is_idempotent(self):
        self.enqueue()
        self.bridge._deliver()
        self.enqueue("Another queued message")
        self.bridge.close()
        self.bridge.close()
        self.assertEqual([item["status"] for item in self.store.get(self.identifier)["messages"]],
                         ["failed", "undelivered"])

    def test_acknowledged_requests_do_not_starve_later_tool_records(self):
        for number in range(70):
            identifier = f"{number:032x}"
            name = identifier + ".json"
            runtime.write_json(self.bridge.outbox / "requests" / name, {
                "request_id": identifier, "session_id": "ses_fixture",
                "kind": "message", "text": "Already acknowledged.",
            })
            runtime.write_json(self.bridge.outbox / "replies" / name, {"ok": True})
        identifier = f"{70:032x}"
        name = identifier + ".json"
        runtime.write_json(self.bridge.outbox / "requests" / name, {
            "request_id": identifier, "session_id": "ses_fixture",
            "kind": "message", "text": "A later visible message.",
        })
        self.bridge._tool_requests()
        reply = runtime.read_json(self.bridge.outbox / "replies" / name)
        self.assertTrue(reply["ok"])
        self.assertEqual([message["text"] for message in self.store.get(self.identifier)["messages"]],
                         ["A later visible message."])
        self.bridge._tool_requests()
        self.assertEqual(len(self.store.get(self.identifier)["messages"]), 1)

    def test_nonparticipating_process_contract_is_unchanged(self):
        with patch.object(runtime.processes, "run", return_value="result") as execute:
            result = runtime.run(["original", "--model", "test"], cwd=self.root, env={}, timeout=3,
                                 capture_output=True)
        self.assertEqual(result, "result")
        execute.assert_called_once_with(["original", "--model", "test"], cwd=self.root, env={}, timeout=3,
                                        capture_output=True)

    def test_deep_runtime_records_are_atomic_and_readable_on_windows(self):
        from harness2 import config

        path = self.root.joinpath(*(["deep-session-" + "x" * 45] * 5), "reply.json")
        deep_root = self.root / path.relative_to(self.root).parts[0]
        self.assertTrue(deep_root.resolve().is_relative_to(self.root.resolve()))
        self.addCleanup(lambda: shutil.rmtree(config.long_path(deep_root)))
        self.assertGreater(len(str(path)), 300)
        runtime.write_json(path, {"ok": True, "label": "Owen résumé"})
        self.assertEqual(runtime.read_json(path), {"ok": True, "label": "Owen résumé"})
        runtime.write_json(path, {"ok": False})
        self.assertEqual(runtime.read_json(path), {"ok": False})
        self.assertEqual([item.name for item in config.long_path(path.parent).iterdir()], ["reply.json"])

    def test_known_credentials_are_redacted_in_all_checkpoint_fields(self):
        self.bridge.environment.update(HARNESS2_LOCAL_API_KEY="credential-never-public",
                                       OUTPUT_TOKEN_MAX="4096")
        result = self.request(kind="checkpoint", objective="Use credential-never-public safely",
                              planned=["Budget 4096"], completed=["Saw credential-never-public"])
        self.assertTrue(result["ok"])
        view = self.store.get(self.identifier)
        self.assertNotIn("credential-never-public", str(view))
        self.assertEqual(view["recovery"]["planned"], ["Budget 4096"])

    def test_run_wrapper_requires_the_official_task_prompt_before_human_delivery(self):
        from contextlib import nullcontext

        environment = {runtime.CONVERSATION_ENV: self.identifier,
                       runtime.WORKSPACE_ENV: str(self.root)}
        worker = runtime.working_directory(environment, "worker")
        command = ["opencode", "run", "--dir", str(worker), "--model", "local/test", "Official task"]
        with (patch.object(runtime, "SessionBridge") as factory,
              patch.object(runtime, "require_worker_containment"),
              patch.object(runtime, "file_lock", return_value=nullcontext()),
              patch.object(runtime.processes, "run", return_value="completed") as execute):
            bridge = factory.return_value
            bridge.client.attach.return_value = ["attached", "official-task"]
            bridge.client.environment = {"native": "environment"}
            result = runtime.run(command, cwd=self.root, env=environment, timeout=30,
                                 capture_output=True)
        self.assertEqual(result, "completed")
        self.assertTrue(factory.call_args.kwargs["wait_for_initial"])
        self.assertEqual(factory.call_args.args[2], worker)
        bridge.start.assert_called_once()
        bridge.client.attach.assert_called_once_with(command)
        self.assertEqual(execute.call_args.args[0], ["attached", "official-task"])
        self.assertEqual(execute.call_args.kwargs["env"], {"native": "environment"})
        self.assertGreater(execute.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(execute.call_args.kwargs["timeout"], 30)
        bridge.drain.assert_called_once()
        bridge.close.assert_called_once()

    def test_conversational_scheduler_serializes_real_demo_phases_without_changing_selection(self):
        import os
        import threading
        import time
        from contextlib import contextmanager

        from harness2.core import schedule
        from harness2.demo import DemoAdapter, DemoProposer

        selected = []
        for participates in (False, True):
            with self.subTest(participates=participates):
                adapter = DemoAdapter(self.root / ("serial-demo" if participates else "normal-demo"))
                proposer = DemoProposer(adapter.spec.channels["proposer"])
                active, peak = 0, 0
                lock = threading.Lock()

                @contextmanager
                def track():
                    nonlocal active, peak
                    with lock:
                        active += 1
                        peak = max(peak, active)
                    try:
                        time.sleep(0.03)
                        yield
                    finally:
                        with lock:
                            active -= 1

                execute, call = adapter.execute, proposer.call

                def tracked_execute(*args, **kwargs):
                    with track():
                        return execute(*args, **kwargs)

                def tracked_call(*args, **kwargs):
                    with track():
                        return call(*args, **kwargs)

                adapter.execute, proposer.call = tracked_execute, tracked_call
                with patch.dict(os.environ, {runtime.CONVERSATION_ENV: self.identifier if participates else ""}):
                    result = schedule.run_task("ledger-1", 2, adapter=adapter, prop=proposer,
                                               runs_root=adapter.spec.runs_root, modes=("agg", "seq"))
                self.assertEqual(result.status, "ok", result.errors)
                self.assertEqual(active, 0)
                if participates:
                    self.assertEqual(peak, 1, "Only one native worker may be active for a conversation")
                else:
                    self.assertGreater(peak, 1, "Ordinary recursion retains concurrent independent work")
                selected.append({mode: adapter.deliverable_text(Path(selection["chosen_run_dir"]))
                                 for mode, selection in result.selections.items()})
        self.assertEqual(selected[0], selected[1])


if __name__ == "__main__":
    unittest.main()
