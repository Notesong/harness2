# SPDX-License-Identifier: Apache-2.0
"""Real conversation archives with simulated model/session owners; no inference."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import conversation_runtime as runtime
from harness2.conversation_runtime import runtime_root, write_json
from harness2.conversations import ConversationStore
from harness2.profile_runtime import pin_profile
from harness2.profiles import ProfileStore
from harness2.ui.conversations import Conversations
from harness2.ui.jobs import BusyError, JobManager
from harness2.ui.workspace import Workspace

EPHEMERAL = "temporary-conversation-model-key"


class ConversationServiceTests(unittest.TestCase):
    def setUp(self):
        # The worker owners below are substituted; their behavior is portable.
        # Actual working-run participation requires Windows containment.
        self.enterContext(patch.object(runtime, "require_worker_containment"))
        self.temporary = tempfile.TemporaryDirectory(prefix="conversation service résumé ")
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)
        self.jobs = SimpleNamespace(lock=threading.RLock(), jobs={}, active_id=None)
        self.profiles = ProfileStore(self.root)
        self.profiles.create("Owen")
        self.configuration = self.profiles.add_configuration("owen", {"settings": {
            "provider": "local", "local_url": "http://127.0.0.1:8765/v1",
            "local_model": "fixture-model", "local_context": "32768", "local_output": "4096",
            "local_runtime": "managed"}, "identity": {"model": "synthetic"}})
        self.revision = self.profiles.add_revision("owen", {"guardrails.md": "A selected harness rule."},
            domain="technical_writers", experience={"cue": "EXPERIENCE_SENTINEL", "lesson": "Keep private experience out of chat.", "evidence": "A fixture"})
        self.profiles.activate("owen", configuration_id=self.configuration["id"], revision_id=self.revision["id"])
        self.profiles.add_note("owen", text="TECHNICAL_NOTE_SENTINEL")
        self.model_loading = threading.Event()
        self.bridge_loading = threading.Event()
        self.hold_model = self.hold_bridge = False
        self.fail_bridge = self.fail_model_close = False
        self.owners, self.bridges = [], []
        self.env = patch.object(self.workspace, "environment", return_value={"HARNESS2_UI_PROVIDER": "local"})
        self.env.start()
        case = self

        class ModelOwner:
            def __init__(self, environment, *, root, log_path, cancelled):
                self.environment = dict(environment)
                self.environment["HARNESS2_LOCAL_API_KEY"] = EPHEMERAL
                self.environment["HARNESS2_JUDGE_API_KEY"] = EPHEMERAL
                self.cancelled, self.closed, self.close_count = cancelled, False, 0
                case.owners.append(self)

            def start(self):
                case.model_loading.set()
                while case.hold_model and not self.cancelled() and not self.closed:
                    time.sleep(0.005)
                if self.cancelled() or self.closed:
                    raise RuntimeError("Model loading cancelled")
                return self

            def close(self):
                self.close_count += 1
                if case.fail_model_close:
                    raise OSError("Synthetic owned-model cleanup failure")
                self.closed = True

        class Bridge:
            def __init__(self, root, identifier, cwd, environment, *, model, label,
                         resume_session_id=None, session_runtime=None):
                self.store = ConversationStore(root)
                self.identifier, self.cwd, self.environment = identifier, Path(cwd), dict(environment)
                self.model, self.label = model, label
                self.worker_id = uuid.uuid4().hex
                self.resume_session_id = resume_session_id
                self.session_id = resume_session_id or "ses_fixture"
                self.runtime = session_runtime or runtime_root(root, identifier) / "workers" / self.worker_id
                self.runtime.mkdir(parents=True, exist_ok=True)
                self.client = SimpleNamespace(status=lambda: "idle")
                self.failure, self.closed, self.close_count = "", False, 0
                case.bridges.append(self)

            def start(self):
                self.store.bind_worker(self.identifier, self.worker_id, label=self.label, session_id=self.session_id)
                write_json(runtime_root(case.root, self.identifier) / "latest.json", {
                    "workspace": str(self.cwd), "session_runtime": str(self.runtime), "session_id": self.session_id,
                    "model": self.model, "label": self.label,
                })
                case.bridge_loading.set()
                while case.hold_bridge and not self.closed:
                    time.sleep(0.005)
                if self.closed:
                    raise RuntimeError("Session startup cancelled")
                if case.fail_bridge:
                    raise RuntimeError(EPHEMERAL + " private path C:/private/model.gguf")
                return self

            def close(self, reason="Stopped"):
                self.closed, self.close_count = True, self.close_count + 1
                view = self.store.get(self.identifier)
                if any(worker["id"] == self.worker_id for worker in view["workers"]):
                    self.store.end_worker(self.identifier, self.worker_id, reason)

        self.model_patch = patch("harness2.ui.conversations.ManagedLocalServer", ModelOwner)
        self.bridge_patch = patch("harness2.ui.conversations.SessionBridge", Bridge)
        self.model_patch.start()
        self.bridge_patch.start()
        self.service = Conversations(self.workspace, self.jobs)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.hold_model = self.hold_bridge = self.fail_model_close = False
        self.service.close()
        self.bridge_patch.stop()
        self.model_patch.stop()
        self.env.stop()
        self.temporary.cleanup()

    def create(self):
        return self.service.create({"profile_id": "owen", "title": "A synthetic conversation"})

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Conversation service did not reach the expected lifecycle state")

    def running(self, identifier):
        self.service.command(identifier, "start", {})
        self.wait(lambda: self.service.get(identifier)["runtime"]["status"] == "running")

    def stop(self, identifier):
        self.service.command(identifier, "stop", {})
        self.wait(lambda: not self.service.busy)

    def test_create_visit_and_list_pin_identity_without_starting_or_altering_profile(self):
        before = {path: path.read_bytes() for path in self.profiles.root.rglob("*.json")}
        conversation = self.create()
        identifier = conversation["id"]
        self.assertTrue((runtime_root(self.root, identifier) / "profile-start.json").is_file())
        self.assertEqual(conversation["configuration_id"], self.configuration["id"])
        self.assertEqual(conversation["runtime"]["status"], "offline")
        self.assertTrue(conversation["runtime"]["can_start"])
        self.assertEqual(self.service.list()[0]["pending_count"], 0)
        self.service.get(identifier)
        self.assertEqual(self.owners, [])
        self.assertEqual(self.bridges, [])
        self.assertFalse(self.service.busy)
        self.assertEqual({path: path.read_bytes() for path in self.profiles.root.rglob("*.json")}, before)

    def test_start_uses_only_selected_harness_and_temporary_owned_model_environment(self):
        conversation = self.create()
        self.running(conversation["id"])
        self.assertTrue(self.service.busy)
        bridge = self.bridges[-1]
        agents = (bridge.cwd / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("A selected harness rule.", agents)
        self.assertNotIn("EXPERIENCE_SENTINEL", agents)
        self.assertNotIn("TECHNICAL_NOTE_SENTINEL", agents)
        self.assertNotIn("HARNESS2_PROFILE_SNAPSHOT", bridge.environment)
        self.assertNotIn("HARNESS2_RUNS_DIR", bridge.environment)
        self.assertEqual(bridge.environment["HARNESS2_LOCAL_API_KEY"], EPHEMERAL)
        public = json.dumps(self.service.get(conversation["id"]))
        self.assertNotIn(EPHEMERAL, public)
        self.assertNotIn(str(bridge.cwd), public)
        self.assertNotIn(str(bridge.runtime), public)
        self.stop(conversation["id"])
        self.assertTrue(self.owners[-1].closed)
        self.assertTrue(bridge.closed)
        self.assertEqual(self.service.get(conversation["id"])["status"], "open")

    def test_messages_stay_queued_until_runtime_confirms_delivery_and_reply(self):
        identifier = self.create()["id"]
        with self.assertRaises(ValueError):
            self.service.command(identifier, "messages", {"text": "Not connected"})
        self.running(identifier)
        result = self.service.command(identifier, "messages", {"text": "Why this approach?"})
        message = result["messages"][-1]
        self.assertEqual(message["status"], "queued")
        self.assertEqual(result["pending_count"], 1)
        self.service.store.transition(identifier, message["id"], "delivered", session_message_id="msg_native")
        self.service.store.assistant(identifier, "A visible explanation.", worker_id=self.bridges[-1].worker_id,
                                     session_message_id="msg_answer", reply_to=[message["id"]])
        self.assertEqual(self.service.get(identifier)["pending_count"], 0)

    def test_oversize_message_is_rejected_before_queueing_for_native_transport(self):
        identifier = self.create()["id"]
        self.running(identifier)
        before = self.service.store.get(identifier)
        with self.assertRaisesRegex(ValueError, "16,000"):
            self.service.command(identifier, "messages", {"text": "x" * 16_001})
        self.assertEqual(self.service.store.get(identifier), before)
        accepted = self.service.command(identifier, "messages", {"text": "x" * 16_000})
        self.assertEqual(len(accepted["messages"][-1]["text"]), 16_000)
        self.assertEqual(accepted["messages"][-1]["status"], "queued")

    def test_another_idle_session_cannot_start_until_current_owner_stops(self):
        first, second = self.create()["id"], self.create()["id"]
        self.running(first)
        self.assertEqual(self.service.command(first, "start", {})["id"], first)
        self.assertEqual(len(self.owners), 1)
        with self.assertRaises(BusyError):
            self.service.command(second, "start", {})
        self.stop(first)
        self.running(second)
        self.assertEqual(len(self.owners), 2)

    def test_start_failure_closes_both_owners_and_does_not_expose_raw_error(self):
        identifier = self.create()["id"]
        self.fail_bridge = True
        self.service.command(identifier, "start", {})
        self.wait(lambda: not self.service.busy)
        result = self.service.get(identifier)
        self.assertEqual(result["runtime"]["status"], "error")
        self.assertNotIn(EPHEMERAL, json.dumps(result))
        self.assertNotIn("C:/private", json.dumps(result))
        self.assertTrue(self.owners[-1].closed)
        self.assertTrue(self.bridges[-1].closed)
        self.assertIsNone(result["active_worker_id"])

    def test_stop_during_model_loading_closes_owner_and_never_starts_session(self):
        identifier = self.create()["id"]
        self.hold_model = True
        self.service.command(identifier, "start", {})
        self.assertTrue(self.model_loading.wait(3))
        self.assertTrue(self.service.busy)
        self.stop(identifier)
        self.assertTrue(self.owners[-1].closed)
        self.assertEqual(self.bridges, [])
        self.assertEqual(self.service.get(identifier)["runtime"]["status"], "offline")

    def test_stop_during_session_startup_closes_session_and_model(self):
        identifier = self.create()["id"]
        self.hold_bridge = True
        self.service.command(identifier, "start", {})
        self.assertTrue(self.bridge_loading.wait(3))
        self.stop(identifier)
        self.assertTrue(self.owners[-1].closed)
        self.assertTrue(self.bridges[-1].closed)
        self.assertIsNone(self.service.get(identifier)["active_worker_id"])

    def test_cleanup_failure_keeps_new_model_start_blocked_until_retry_succeeds(self):
        identifier = self.create()["id"]
        self.running(identifier)
        self.fail_model_close = True
        self.service.command(identifier, "stop", {})
        self.wait(lambda: self.service.get(identifier)["runtime"]["status"] == "error")
        self.assertTrue(self.service.busy)
        self.assertFalse(self.service.get(identifier)["runtime"]["can_start"])
        with self.assertRaises(BusyError):
            self.service.command(self.create()["id"], "start", {})
        self.fail_model_close = False
        self.stop(identifier)
        self.assertTrue(self.owners[-1].closed)

    def test_inactivity_unloads_and_explicit_continue_resumes_exact_retained_session(self):
        identifier = self.create()["id"]
        with patch("harness2.ui.conversations.IDLE_TIMEOUT", 0.06), patch("harness2.ui.conversations.POLL_INTERVAL", 0.01):
            self.running(identifier)
            prior = self.bridges[-1]
            self.wait(lambda: not self.service.busy)
        self.assertTrue(prior.closed and self.owners[-1].closed)
        self.running(identifier)
        continued = self.bridges[-1]
        self.assertEqual(continued.resume_session_id, prior.session_id)
        self.assertEqual(continued.cwd, prior.cwd)
        self.assertEqual(continued.runtime, prior.runtime)
        self.assertNotEqual(continued.worker_id, prior.worker_id)

    def test_startup_reconciles_stale_workers_and_does_not_load_models(self):
        identifier = self.create()["id"]
        self.service.store.bind_worker(identifier, "stale-worker", label="Old worker", session_id="ses_old")
        queued = self.service.store.enqueue(identifier, "Queued before app restart")
        delivered = self.service.store.enqueue(identifier, "Sent before app restart")
        self.service.store.transition(identifier, delivered["id"], "delivered")
        restored = Conversations(self.workspace, self.jobs)
        self.addCleanup(restored.close)
        view = restored.get(identifier)
        self.assertEqual(view["messages"][0]["id"], queued["id"])
        self.assertEqual([message["status"] for message in view["messages"]], ["undelivered", "failed"])
        self.assertIsNone(view["active_worker_id"])
        self.assertEqual(view["runtime"]["status"], "offline")
        self.assertEqual(self.owners, [])

    def test_run_lifecycle_links_provenance_and_gates_idle_operations(self):
        output = self.root / "runs" / "synthetic"
        pinned = pin_profile(self.profiles, "owen", output)
        record = {"id": "a" * 12, "title": "Synthetic run", "status": "running"}
        identifier = self.service.begin_run(record, pinned)
        self.jobs.jobs[record["id"]], self.jobs.active_id = record, record["id"]
        env = self.service.run_environment(record, {"ordinary": "value"})
        self.assertEqual(env["HARNESS2_CONVERSATION_ID"], identifier)
        self.assertEqual(env["HARNESS2_CONVERSATION_WORKSPACE"], str(self.root))
        self.assertEqual(env["ordinary"], "value")
        with self.assertRaises(BusyError):
            self.service.command(identifier, "start", {})
        with self.assertRaises(BusyError):
            self.service.command(identifier, "stop", {})
        unrelated = self.create()["id"]
        with self.assertRaises(BusyError):
            self.service.command(unrelated, "messages", {"text": "Unrelated contact"})
        self.service.store.bind_worker(identifier, "run-worker", label="A real working phase", session_id="ses_run")
        sent = self.service.command(identifier, "messages", {"text": "A question for the run"})["messages"][-1]
        self.service.store.transition(identifier, sent["id"], "delivered", session_message_id="msg_run")
        self.service.end_run(record)
        self.assertTrue(record["conversation"]["human_assisted"])
        self.assertEqual(record["conversation"]["human_messages_delivered"], 1)
        self.assertEqual(self.service.store.get(identifier)["status"], "open")
        self.assertEqual(self.service.store.get(identifier)["messages"][-1]["status"], "failed")
        record["status"], self.jobs.active_id = "completed", None
        with self.assertRaisesRegex(ValueError, "no retained native session"):
            self.service.command(identifier, "start", {})
        self.assertEqual(self.owners, [])
        resumed = {"id": "b" * 12, "resumed_from": record["id"], "title": "Resume"}
        self.assertEqual(self.service.begin_run(resumed, pinned), identifier)
        self.assertEqual(resumed["conversation_id"], identifier)

    def test_run_disconnection_is_reported_without_claiming_new_worker_is_loading(self):
        pinned = pin_profile(self.profiles, "owen", self.root / "runs" / "synthetic")
        record = {"id": "a" * 12, "title": "Synthetic run", "status": "running"}
        identifier = self.service.begin_run(record, pinned)
        self.jobs.jobs[record["id"]], self.jobs.active_id = record, record["id"]
        self.service.store.bind_worker(identifier, "failed-worker", label="Failed connection", session_id="ses_failed")
        self.service.store.end_worker(identifier, "failed-worker", "The session connection failed: a private transport reason")
        runtime = self.service.get(identifier)["runtime"]
        self.assertEqual(runtime["status"], "error")
        self.assertFalse(runtime["can_message"])
        self.assertNotIn("private transport reason", json.dumps(runtime))

    def test_nonlocal_run_configuration_is_rejected_before_archiving_a_conversation(self):
        pinned = pin_profile(self.profiles, "owen", self.root / "runs" / "synthetic")
        pinned["configuration"]["settings"]["provider"] = "vertex"
        before = self.service.list()
        with self.assertRaisesRegex(ValueError, "local model profile"):
            self.service.begin_run({"id": "a" * 12, "title": "Unsupported provider"}, pinned)
        self.assertEqual(self.service.list(), before)
        self.assertEqual(self.owners, [])

    def test_unsupported_run_containment_is_rejected_before_conversation_archive(self):
        pinned = pin_profile(self.profiles, "owen", self.root / "runs" / "synthetic")
        before = {path: path.read_bytes() for path in self.root.rglob("*.json")}
        record = {"id": "a" * 12, "title": "Unsupported containment"}
        with patch.object(runtime, "require_worker_containment", side_effect=ValueError("Windows containment required")) as gate:
            with self.assertRaisesRegex(ValueError, "Windows containment"):
                self.service.begin_run(record, pinned)
        gate.assert_called_once_with()
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*.json")}, before)
        self.assertNotIn("conversation_id", record)
        self.assertEqual(self.owners, [])

    def test_unsupported_job_containment_rejects_new_resume_and_preview_before_mutation(self):
        manager = JobManager(self.workspace)
        self.addCleanup(manager.close)
        params = manager.parameters({"profile_id": "owen", "conversation_enabled": True})
        previous = {"id": "a" * 12, "kind": "benchmark", "status": "completed", "params": params,
                    "output_dir": str(self.root / "missing-prior-output"), "profile": {"profile_id": "owen"}}
        manager.jobs[previous["id"]] = previous
        for kind, options in (("benchmark", {}), ("preview", {}), ("benchmark", {"resume_id": previous["id"]})):
            with self.subTest(kind=kind, options=options):
                before = {path: path.read_bytes() for path in self.root.rglob("*.json")}
                # Resume's existing snapshot is read-only; no run should be
                # created or pinned before the containment policy is checked.
                with (patch.object(runtime, "require_worker_containment", side_effect=ValueError("Windows containment required")) as gate,
                      patch("harness2.profile_runtime.load_snapshot", return_value={}),
                      patch("harness2.profile_runtime.pin_profile") as pin,
                      patch("harness2.ui.jobs.threading.Thread") as worker):
                    with self.assertRaisesRegex(ValueError, "Windows containment"):
                        manager.start(kind, params, confirmed=True, **options)
                gate.assert_called_once_with()
                pin.assert_not_called()
                worker.assert_not_called()
                self.assertIsNone(manager.active_id)
                self.assertEqual(set(manager.jobs), {previous["id"]})
                self.assertFalse(self.workspace.runs_root.exists())
                self.assertFalse((self.root / "missing-prior-output").exists())
                self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*.json")}, before)

    def test_retained_session_paths_and_model_must_match_before_inference(self):
        identifier = self.create()["id"]
        self.running(identifier)
        self.stop(identifier)
        root = runtime_root(self.root, identifier)
        saved = json.loads((root / "latest.json").read_text(encoding="utf-8"))
        outside = self.root / "outside"
        outside.mkdir()
        write_json(root / "latest.json", {**saved, "workspace": str(outside)})
        with self.assertRaisesRegex(ValueError, "inside its conversation runtime"):
            self.service.command(identifier, "start", {})
        self.assertEqual(len(self.owners), 1)
        write_json(root / "latest.json", {**saved, "model": "local/a-different-model"})
        self.service.command(identifier, "start", {})
        self.wait(lambda: not self.service.busy)
        self.assertEqual(self.service.get(identifier)["runtime"]["status"], "error")
        self.assertEqual(len(self.owners), 1)

    def test_app_close_cancels_loading_without_creating_orphan_resources(self):
        identifier = self.create()["id"]
        self.hold_model = True
        self.service.command(identifier, "start", {})
        self.assertTrue(self.model_loading.wait(3))
        self.service.close()
        self.assertTrue(self.owners[-1].closed)
        self.assertEqual(self.bridges, [])
        self.assertFalse(self.service.busy)
        self.assertFalse(self.service.get(identifier)["runtime"]["can_start"])


class WorkerContainmentPolicyTests(unittest.TestCase):
    def test_only_windows_allows_nested_working_run_containment(self):
        with patch.object(runtime, "os", SimpleNamespace(name="nt")):
            self.assertIsNone(runtime.require_worker_containment())
        with patch.object(runtime, "os", SimpleNamespace(name="posix")):
            with self.assertRaisesRegex(ValueError, "require Windows.*containment"):
                runtime.require_worker_containment()

    def test_direct_opted_in_runtime_rejects_before_files_or_processes(self):
        environment = {runtime.CONVERSATION_ENV: "a" * 32,
                       runtime.WORKSPACE_ENV: str(Path.cwd())}
        with (patch.object(runtime, "os", SimpleNamespace(name="posix")),
              patch.object(runtime, "runtime_root") as files,
              patch.object(runtime.processes, "run") as launch):
            with self.assertRaisesRegex(ValueError, "require Windows.*containment"):
                runtime.run(["unused"], cwd=Path.cwd(), env=environment, timeout=1)
        files.assert_not_called()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
