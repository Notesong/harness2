# SPDX-License-Identifier: Apache-2.0
"""Profile boundaries with real archives/adapters/children, without model calls.

Only the JobBench task launcher and Studio model lease are substituted. Archive
publication, profile selection, subprocess environment, run references, and the
JobBench adapter's baseline materialisation remain real.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import tree
from harness2.profile_import import tool_policy_fingerprint
from harness2.profile_runtime import (
    ProfileAdapter,
    apply_profile,
    candidate_from_run,
    configuration_environment,
    load_snapshot,
    pin_profile,
    profile_run_outcome,
    run_environment,
    verify_configuration,
)
from harness2.profiles import ProfileStore
from harness2.substrate.opencode import OPENCODE_SURFACE
from harness2.ui.jobs import JobManager
from harness2.ui.profiles import Profiles
from harness2.ui.workspace import Workspace


class ProfileRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="profile runtime résumé ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ProfileStore(self.root)
        self.store.create("Owen")
        self.weights = self.root / "test.gguf"
        self.runtime = self.root / "test-runtime.exe"
        self.weights.write_bytes(b"model bytes, never loaded")
        self.runtime.write_bytes(b"runtime bytes, never executed")
        self.configuration = self.store.add_configuration("owen", self.configuration_snapshot())
        self.revision = self.store.add_revision(
            "owen", {"guardrails.md": "Write deliverables before rereading."},
            domain="technical_writers", experience={
                "cue": "Technical documentation", "lesson": "Externalize progress early.",
                "evidence": "One partial probe; transfer remains unknown.",
                "confidence": "suspected", "edits": "Track completed deliverables.",
            },
        )
        self.store.activate("owen", configuration_id=self.configuration["id"],
                            revision_id=self.revision["id"])
        self.output = self.root / "runs" / "first"

    def configuration_snapshot(self, *, model="owen-test", runtime="external"):
        return {
            "settings": {"provider": "local", "local_url": "http://127.0.0.1:8087/v1",
                         "local_model": model, "local_context": "65536", "local_output": "8192",
                         "local_runtime": runtime, "local_weights": str(self.weights),
                         "local_executable": str(self.runtime)},
            "identity": {"weights": {"sha256": hashlib.sha256(self.weights.read_bytes()).hexdigest()},
                         "runtime": {"sha256": hashlib.sha256(self.runtime.read_bytes()).hexdigest()}},
        }

    def pin(self):
        return pin_profile(self.store, "owen", self.output)

    def adapter(self, snapshot):
        return ProfileAdapter(JobBenchAdapter(store_root=self.output / "jb" / "dataset"),
                              snapshot, self.output)

    def saved_candidate(self, *, label="agg1", mode="agg"):
        output = self.root / "runs" / "completed"
        task = output / "jb" / "adapt" / "jb-oc-agg1" / "technical_writers__task2"
        harness = task / "harnesses" / label
        harness.mkdir(parents=True)
        (harness / "guardrails.md").write_text("A new source verification rule.", encoding="utf-8")
        summary = {"task_id": "technical_writers/task2", "domain": "technical_writers",
                   "selections": {mode: {"ok": True, "chosen_label": label,
                       "experience": {"cue": "Technical work", "lesson": "Verify sources.",
                                      "evidence": "One selected run."}}}}
        (task / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (task / "judged.json").write_text('{"score":0.99}', encoding="utf-8")
        record = {"id": "012345abcdef", "title": "Finished work", "kind": "benchmark",
                  "status": "succeeded", "output_dir": str(output),
                  "params": {"benchmark": "jb", "substrate": "oc"}}
        return record, task, harness

    def link_directory(self, link, target):
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, check=False)
            if result.returncode:
                self.skipTest("Windows junction creation unavailable.")
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_pinned_start_ignores_later_active_changes_and_cannot_be_replaced(self):
        original = self.pin()
        original_bytes = (self.output / "profile-start.json").read_bytes()
        next_configuration = self.store.add_configuration("owen", self.configuration_snapshot(model="other-alias"))
        next_revision = self.store.add_revision("owen", {"guardrails.md": "A different approach."},
                                                 parents=[self.revision["id"]])
        self.store.activate("owen", configuration_id=next_configuration["id"], revision_id=next_revision["id"])
        pinned = load_snapshot(self.output / "profile-start.json")
        self.assertEqual(pinned, original)
        self.assertEqual(run_environment({}, self.output)["HARNESS2_LOCAL_MODEL"], "owen-test")
        self.assertEqual(pin_profile(self.store, "owen", self.output,
            configuration_id=self.configuration["id"], revision_id=self.revision["id"]), original)
        with self.assertRaises(ValueError):
            self.pin()
        self.assertEqual((self.output / "profile-start.json").read_bytes(), original_bytes)

    def test_tampered_pinned_tree_is_rejected_before_adapter_or_environment(self):
        self.pin()
        path = self.output / "profile-start.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["harness"]["guardrails.md"] = "Tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_snapshot(path)
        with self.assertRaises(ValueError):
            run_environment({}, self.output)

    def test_interrupted_atomic_pin_leaves_no_target_and_can_retry(self):
        with patch("harness2.profile_runtime.atomic_replace", side_effect=OSError("Publication interrupted")):
            with self.assertRaises(OSError):
                self.pin()
        self.assertFalse((self.output / "profile-start.json").exists())
        self.assertEqual(list(self.output.glob(".profile-start.json-*.tmp")), [])
        pinned = self.pin()
        self.assertEqual(load_snapshot(self.output / "profile-start.json"), pinned)
        original_bytes = (self.output / "profile-start.json").read_bytes()
        with patch("harness2.profile_runtime.atomic_replace", side_effect=AssertionError("Must not rewrite")):
            self.assertEqual(self.pin(), pinned)
        self.assertEqual((self.output / "profile-start.json").read_bytes(), original_bytes)

    def test_omitted_always_on_files_materialize_and_resume_consistently(self):
        pinned = self.pin()
        first = self.adapter(pinned)
        second = self.adapter(load_snapshot(self.output / "profile-start.json"))
        self.assertEqual(first.base_harness(), second.base_harness())
        self.assertEqual((second.base_harness() / "guardrails.md").read_text(encoding="utf-8"),
                         pinned["harness"]["guardrails.md"])
        (second.base_harness() / "guardrails.md").write_text("Tampered", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.adapter(pinned)

    def test_real_jobbench_execute_none_uses_pinned_harness_and_candidate_stays_explicit(self):
        wrapped = self.adapter(self.pin())
        actual = wrapped._adapter
        captured = []
        original_materialise = actual.substrate.materialise

        def materialise(source, spec, extra):
            captured.append(tree.read_tree(OPENCODE_SURFACE, source))
            return original_materialise(source, spec, extra)

        def fake_executor(task_id, installation, run_ids, timeout):
            # This is the sole task/model execution boundary replaced here.
            self.assertTrue(installation.staging_dir.is_dir())
            output = actual._out_dir(task_id, run_ids[0].replace("/", "__"))
            output.mkdir(parents=True)
            (output / "deliverable.md").write_text("A deterministic test deliverable.", encoding="utf-8")
            return [output]

        candidate = self.root / "candidate"
        tree.write_tree(OPENCODE_SURFACE, {"guardrails.md": "Explicit candidate."}, candidate)
        with patch.object(actual, "_ensure_store_task"), \
                patch.object(actual.substrate, "materialise", side_effect=materialise), \
                patch.object(actual.substrate, "execute", side_effect=fake_executor):
            baseline_output = wrapped.execute("technical_writers/task2", None, "test/base0")
            candidate_output = wrapped.execute("technical_writers/task2", candidate, "test/dom1")
        self.assertTrue((baseline_output / "deliverable.md").is_file())
        self.assertTrue((candidate_output / "deliverable.md").is_file())
        self.assertEqual(captured[0]["guardrails.md"], self.revision["tree"]["guardrails.md"])
        self.assertEqual(captured[1]["guardrails.md"], "Explicit candidate.")
        self.assertEqual(self.store.get("owen")["active_revision_id"], self.revision["id"])

    def test_experience_enters_matching_domain_context_without_notes_grades_or_history(self):
        self.store.add_note("owen", text="HUMAN_GRADE_AND_HISTORY_MUST_STAY_OUT", evidence_status="observed")
        wrapped = self.adapter(self.pin())
        wrong_context = self.root / "wrong-context"
        self.assertFalse(wrapped.populate_profile_context("accountants/task2", wrong_context))
        self.assertFalse(wrong_context.exists())
        right_context = self.root / "right-context"
        self.assertTrue(wrapped.populate_profile_context("technical_writers/task2", right_context))
        texts = "\n".join(p.read_text(encoding="utf-8") for p in right_context.rglob("*") if p.is_file())
        self.assertIn("Externalize progress early.", texts)
        self.assertIn("benefit on this task is unproven", texts)
        self.assertNotIn("HUMAN_GRADE_AND_HISTORY_MUST_STAY_OUT", texts)
        self.assertNotIn("snapshot_hash", texts)
        self.assertEqual(list(right_context.rglob("judged.json")), [])

    def test_environment_replaces_old_aliases_runtime_and_endpoint_preserving_only_auth(self):
        old = {"HARNESS2_SOLVER_MODEL": "google-vertex/old", "HARNESS2_IMPROVER_MODEL": "old",
               "HARNESS2_JUDGE_MODEL": "old", "HARNESS2_JUDGE_BASE_URL": "https://old.example/v1",
               "HARNESS2_LOCAL_MODEL": "old", "HARNESS2_LOCAL_RUNTIME": "managed",
               "HARNESS2_LOCAL_API_KEY": "local-private-key", "PATH": "keep-existing-path"}
        original = dict(old)
        resolved = configuration_environment(old, self.configuration["snapshot"])
        self.assertEqual(old, original)
        self.assertEqual(resolved["HARNESS2_SOLVER_MODEL"], "local/owen-test")
        self.assertEqual(resolved["HARNESS2_IMPROVER_MODEL"], "local/owen-test")
        self.assertEqual(resolved["HARNESS2_JUDGE_MODEL"], "owen-test")
        self.assertEqual(resolved["HARNESS2_JUDGE_BASE_URL"], "http://127.0.0.1:8087/v1")
        self.assertEqual(resolved["HARNESS2_LOCAL_RUNTIME"], "external")
        self.assertEqual(resolved["HARNESS2_LOCAL_API_KEY"], "local-private-key")
        self.assertEqual(resolved["PATH"], "keep-existing-path")

    def test_current_bytes_must_match_configuration_identity(self):
        snapshot = self.configuration["snapshot"]
        environment = configuration_environment({}, snapshot)
        verify_configuration(snapshot, environment)
        self.weights.write_bytes(b"Another model behind the same alias")
        with self.assertRaisesRegex(ValueError, "weights"):
            verify_configuration(snapshot, environment)
        self.weights.write_bytes(b"model bytes, never loaded")
        self.runtime.write_bytes(b"Another runtime behind the same filename")
        with self.assertRaisesRegex(ValueError, "runtime"):
            verify_configuration(snapshot, environment)

    def test_declared_tool_policy_rejects_changes_but_allows_authentication_rotation(self):
        policy = {"provider": {"local": {"options": {"apiKey": "first-private-value"}}},
                  "tools": {"webfetch": False}, "permission": {"bash": "deny"}}
        environment = {"OPENCODE_CONFIG_CONTENT": json.dumps(policy),
                       "HARNESS2_LOCAL_API_KEY": "first-private-value"}
        snapshot = copy.deepcopy(self.configuration["snapshot"])
        snapshot["identity"]["tools"] = tool_policy_fingerprint(environment)
        verify_configuration(snapshot, configuration_environment(environment, snapshot))
        policy["provider"]["local"]["options"]["apiKey"] = "rotated-private-value"
        rotated = {"OPENCODE_CONFIG_CONTENT": json.dumps(policy),
                   "HARNESS2_LOCAL_API_KEY": "rotated-private-value"}
        verify_configuration(snapshot, configuration_environment(rotated, snapshot))
        self.assertNotIn("first-private-value", str(snapshot))
        policy["tools"]["webfetch"] = True
        changed = {**rotated, "OPENCODE_CONFIG_CONTENT": json.dumps(policy)}
        with self.assertRaisesRegex(ValueError, "tool policy"):
            verify_configuration(snapshot, configuration_environment(changed, snapshot))

    def test_undeclared_tool_policy_cannot_silently_become_declared(self):
        snapshot = copy.deepcopy(self.configuration["snapshot"])
        snapshot["identity"]["tools"] = tool_policy_fingerprint({})
        self.assertIsNone(snapshot["identity"]["tools"]["declared_configuration_sha256"])
        verify_configuration(snapshot, configuration_environment({}, snapshot))
        changed = {"OPENCODE_CONFIG_CONTENT": '{"tools":{"webfetch":true}}'}
        with self.assertRaisesRegex(ValueError, "tool policy"):
            verify_configuration(snapshot, configuration_environment(changed, snapshot))

    def test_apply_profile_rejects_other_cells_and_snapshot_from_another_run(self):
        self.pin()
        environment = run_environment({}, self.output)
        self.assertIsInstance(apply_profile(JobBenchAdapter(), environment), ProfileAdapter)
        with self.assertRaises(ValueError):
            apply_profile(JobBenchAdapter(substrate="cx"), environment)
        environment["HARNESS2_RUNS_DIR"] = str(self.root / "different-run")
        with self.assertRaises(ValueError):
            apply_profile(JobBenchAdapter(), environment)

    def test_child_boundary_rejects_drifted_model_roles_runtime_and_budgets(self):
        self.pin()
        original = run_environment({}, self.output)
        for name, changed in (
            ("HARNESS2_LOCAL_MODEL", "another-model"),
            ("HARNESS2_SOLVER_MODEL", "google-vertex/unrelated-model"),
            ("HARNESS2_IMPROVER_MODEL", "local/another-model"),
            ("HARNESS2_JUDGE_MODEL", "another-judge"),
            ("HARNESS2_JUDGE_BASE_URL", "http://127.0.0.1:9999/v1"),
            ("HARNESS2_LOCAL_RUNTIME", "managed"),
            ("HARNESS2_LOCAL_CONTEXT", "32768"),
            ("HARNESS2_LOCAL_OUTPUT", "4096"),
        ):
            with self.subTest(variable=name):
                drifted = {**original, name: changed}
                with self.assertRaises(ValueError):
                    apply_profile(JobBenchAdapter(), drifted)
        rotated_auth = {**original, "HARNESS2_LOCAL_API_KEY": "fresh-runtime-secret",
                        "HARNESS2_JUDGE_API_KEY": "fresh-runtime-secret"}
        self.assertIsInstance(apply_profile(JobBenchAdapter(), rotated_auth), ProfileAdapter)

    def test_from_run_copies_candidate_read_only_and_does_not_adopt_or_copy_grade(self):
        record, _, _ = self.saved_candidate()
        output = Path(record["output_dir"])
        before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
        candidate = candidate_from_run(self.store, "owen", record, parents=[self.revision["id"]])
        self.assertEqual(candidate["parents"], [self.revision["id"]])
        self.assertEqual(candidate["experience"]["lesson"], "Verify sources.")
        self.assertNotIn("score", str(candidate))
        self.assertEqual(self.store.get("owen")["active_revision_id"], self.revision["id"])
        self.assertEqual({p: p.read_bytes() for p in output.rglob("*") if p.is_file()}, before)

    def test_finished_outcome_and_selected_candidate_survive_source_rewrite(self):
        record, task, harness = self.saved_candidate()
        output = Path(record["output_dir"])
        binding = pin_profile(self.store, "owen", output)
        path = task / "summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary.update(status="completed", raw_notes="PRIVATE_RAW_NOTES_SENTINEL", grade="PRIVATE_GRADE_SENTINEL")
        summary["modes"] = {"agg": {"ok": True, "stages": [
            {"label": "dom1", "fell_back": False, "kept_base": False, "attempts": 1,
             "notes": "PRIVATE_STAGE_NOTES_SENTINEL", "error": ""},
        ]}}
        path.write_text(json.dumps(summary), encoding="utf-8")
        before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
        reference = profile_run_outcome(self.store, binding, output, record)
        archived = self.store.get("owen")
        self.assertEqual(len(reference["candidate_revision_ids"]), 1)
        candidate_id = reference["candidate_revision_ids"][0]
        candidate = next(row for row in archived["revisions"] if row["id"] == candidate_id)
        self.assertEqual(candidate["parents"], [self.revision["id"]])
        self.assertEqual(candidate["tree"]["guardrails.md"], "A new source verification rule.")
        self.assertEqual(candidate["experience"]["lesson"], "Verify sources.")
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(archived["active_revision_id"], self.revision["id"])
        self.assertEqual(reference["outcomes"][0]["status"], "completed")
        self.assertEqual(reference["outcomes"][0]["modes"][0]["stages"][0]["attempts"], 1)
        self.assertEqual({p: p.read_bytes() for p in output.rglob("*") if p.is_file()}, before)
        self.assertNotIn("PRIVATE_", str(archived))
        frozen_history = {p: p.read_bytes() for p in self.store.root.rglob("*.json")}
        path.write_text('{"status":"rewritten during resume"}', encoding="utf-8")
        (harness / "guardrails.md").write_text("Working state was pruned.", encoding="utf-8")
        latest = self.store.get("owen")
        self.assertEqual(latest["runs"][0]["outcomes"], reference["outcomes"])
        copied = next(row for row in latest["revisions"] if row["id"] == candidate_id)
        self.assertEqual(copied["tree"]["guardrails.md"], "A new source verification rule.")
        for file, content in frozen_history.items():
            self.assertEqual(file.read_bytes(), content)

    def test_failed_outcome_preserves_fallback_timeout_facts_without_candidate_or_grade(self):
        record, task, _ = self.saved_candidate()
        record["status"] = "failed"
        output = Path(record["output_dir"])
        binding = pin_profile(self.store, "owen", output)
        summary = {"task_id": "technical_writers/task2", "domain": "technical_writers",
                   "status": "failed", "selections": {}, "grade": "PRIVATE_GRADE_SENTINEL",
                   "errors": ["PRIVATE_ERROR_SENTINEL"], "modes": {"agg": {
                       "ok": False, "stages": [{"label": "inv1", "fell_back": True,
                           "kept_base": False, "attempts": 2,
                           "notes": "PRIVATE_STAGE_NOTES_SENTINEL",
                           "error": "Timed out after retries: PRIVATE_ERROR_SENTINEL"}],
                   }}}
        (task / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        reference = profile_run_outcome(self.store, binding, output, record)
        self.assertEqual(reference["candidate_revision_ids"], [])
        self.assertEqual(reference["status"], "failed")
        stage = reference["outcomes"][0]["modes"][0]["stages"][0]
        self.assertTrue(stage["fell_back"])
        self.assertEqual(stage["failure"], "timeout")
        self.assertEqual(stage["attempts"], 2)
        archive = self.store.get("owen")
        self.assertEqual(len(archive["revisions"]), 1)
        self.assertEqual(archive["runs"][0]["outcomes"], reference["outcomes"])
        self.assertNotIn("PRIVATE_", str(archive))

    def test_from_run_rejects_escaped_labels_and_linked_harnesses(self):
        record, task, harness = self.saved_candidate()
        summary_path = task / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        # Even a destination elsewhere inside the experiment is not this task's
        # selected harness; traversal must not silently become a valid import.
        sibling = task / "other"
        sibling.mkdir()
        (sibling / "guardrails.md").write_text("Unrelated files", encoding="utf-8")
        summary["selections"]["agg"]["chosen_label"] = "../other"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaises(ValueError):
            candidate_from_run(self.store, "owen", record)
        summary["selections"]["agg"]["chosen_label"] = "linked"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        self.link_directory(harness.parent / "linked", sibling)
        with self.assertRaises(ValueError):
            candidate_from_run(self.store, "owen", record)

    def test_conversation_influence_requires_selected_stage_declaration_and_no_raw_chat(self):
        from harness2.conversations import ConversationStore

        record, task, _ = self.saved_candidate()
        conversations = ConversationStore(self.root)
        conversation = conversations.create("owen", self.configuration["id"], self.revision["id"],
                                            run_id=record["id"])
        identifier = conversation["id"]
        conversations.bind_worker(identifier, "worker", label="Proposal", session_id="ses_fixture")
        source = conversations.enqueue(identifier, "PRIVATE_CHAT_SENTINEL: draft files earlier.")
        decision = conversations.record_decision(identifier, text="PRIVATE_DECISION_SENTINEL", why="Human suggestion.",
                                                  source_message_ids=[source["id"]], status="adopted")
        record["conversation_id"] = identifier
        path = task / "summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["modes"] = {"agg": {"stages": [{"label": "discarded", "notes":
            "CONVERSATION_DECISION: " + decision["id"]}]}}
        path.write_text(json.dumps(summary), encoding="utf-8")
        unrelated = candidate_from_run(self.store, "owen", record)
        self.assertEqual(unrelated["motivated_by"], [])
        summary["modes"]["agg"]["stages"].append({"label": "agg1", "notes":
            "CONVERSATION_DECISION: " + decision["id"]})
        path.write_text(json.dumps(summary), encoding="utf-8")
        linked = candidate_from_run(self.store, "owen", record)
        self.assertEqual(linked["motivated_by"][0]["decision_id"], decision["id"])
        self.assertEqual(linked["motivated_by"][0]["source_message_ids"], [source["id"]])
        self.assertNotIn("PRIVATE_", str(self.store.get("owen")))

    @unittest.skipIf(os.name == "nt", "Case-distinct source filenames require a case-sensitive filesystem.")
    def test_from_run_rejects_case_colliding_files_before_surface_filter(self):
        record, _, harness = self.saved_candidate()
        (harness / "GUARDRAILS.md").write_text("A conflicting alias.", encoding="utf-8")
        with self.assertRaises(ValueError):
            candidate_from_run(self.store, "owen", record)

    def test_studio_simplification_and_borrowing_create_candidates_without_adoption(self):
        workspace = Workspace(self.root)
        jobs = JobManager(workspace)
        self.addCleanup(jobs.close)
        controller = Profiles(workspace, jobs)
        controller.command("owen", "revisions", {"parent_id": self.revision["id"],
                                                "remove_paths": ["guardrails.md"]})
        current = self.store.get("owen")
        self.assertEqual(current["active_revision_id"], self.revision["id"])
        self.assertEqual(current["revisions"][-1]["tree"], {})
        self.store.create("Second model")
        controller.command("second-model", "import", {"source_profile_id": "owen",
                                                        "revision_id": self.revision["id"]})
        second = self.store.get("second-model")
        self.assertIsNone(second["active_revision_id"])
        self.assertEqual(second["revisions"][0]["operation"], "import")

    def test_real_studio_child_uses_pinned_configuration_when_active_selection_changes(self):
        workspace = Workspace(self.root)
        manager = JobManager(workspace)
        self.addCleanup(manager.close)
        child_output = self.root / "child-observation.json"
        model_started, release_start = threading.Event(), threading.Event()
        owners = []

        class ModelLease:
            def __init__(self, environment, **kwargs):
                self.environment = dict(environment)
                self.pid = 12345  # Fake metadata; no PID is stopped by this lease.
                self.closed = False
                owners.append(self)

            def start(self):
                model_started.set()
                if not release_start.wait(timeout=10):
                    raise RuntimeError("Test model lease never released")
                return self

            def close(self):
                self.closed = True

        managed = self.store.add_configuration("owen", self.configuration_snapshot(runtime="managed"))
        self.store.activate("owen", configuration_id=managed["id"])
        tested_candidate = self.store.add_revision("owen", {"guardrails.md": "A candidate, not adopted."},
                                                    parents=[self.revision["id"]])
        program = (
            "import json,os,sys; from pathlib import Path; "
            "keys=['HARNESS2_LOCAL_MODEL','HARNESS2_SOLVER_MODEL','HARNESS2_LOCAL_RUNTIME',"
            "'HARNESS2_PROFILE_SNAPSHOT']; "
            "observed={k:os.environ.get(k) for k in keys}; "
            "observed['snapshot_revision_id']=json.loads(Path(os.environ['HARNESS2_PROFILE_SNAPSHOT']).read_text())['revision_id']; "
            "Path(sys.argv[1]).write_text(json.dumps(observed))"
        )
        phase = {"id": "observe", "label": "Observe pinned child environment", "status": "pending",
                 "command": [sys.executable, "-u", "-c", program, str(child_output)]}
        with patch.object(workspace, "benchmarks", return_value=[]), \
                patch.object(workspace, "environment", return_value={"PATH": os.environ.get("PATH", ""),
                    "HARNESS2_RUNS_DIR": str(self.root / "runs"), "HARNESS2_UI_PROVIDER": "local"}), \
                patch.object(workspace, "secrets", return_value=[]), \
                patch.object(manager, "_plan", return_value=[copy.deepcopy(phase)]), \
                patch("harness2.ui.jobs.ManagedLocalServer", ModelLease):
            record = manager.start("benchmark", {"profile_id": "owen",
                "profile_revision_id": tested_candidate["id"]}, confirmed=True)
            self.assertTrue(model_started.wait(timeout=5))
            try:
                new_configuration = self.store.add_configuration("owen", self.configuration_snapshot(model="later-model"))
                self.store.activate("owen", configuration_id=new_configuration["id"])
            finally:
                release_start.set()
            manager.threads[record["id"]].join(timeout=15)
            self.assertFalse(manager.threads[record["id"]].is_alive())
        finished = manager.get(record["id"])
        self.assertEqual(finished["status"], "succeeded", finished)
        observed = json.loads(child_output.read_text(encoding="utf-8"))
        self.assertEqual(observed["HARNESS2_LOCAL_MODEL"], "owen-test")
        self.assertEqual(observed["HARNESS2_SOLVER_MODEL"], "local/owen-test")
        self.assertEqual(observed["HARNESS2_LOCAL_RUNTIME"], "managed")
        self.assertEqual(observed["snapshot_revision_id"], tested_candidate["id"])
        self.assertEqual(finished["profile"]["configuration_id"], managed["id"])
        self.assertTrue(owners[0].closed)
        archive = self.store.get("owen")
        self.assertEqual(len(archive["runs"]), 1)
        self.assertEqual(archive["runs"][0]["status"], "succeeded")
        self.assertEqual(archive["runs"][0]["configuration_id"], managed["id"])
        self.assertEqual(archive["runs"][0]["revision_id"], tested_candidate["id"])
        self.assertEqual(archive["active_revision_id"], self.revision["id"])

    def test_replaced_snapshot_during_model_cleanup_cannot_redirect_final_run_history(self):
        self.store.create("Other profile")
        other_configuration = self.store.add_configuration("other-profile", self.configuration_snapshot())
        other_revision = self.store.add_revision("other-profile", {"guardrails.md": "Other profile's baseline."})
        self.store.activate("other-profile", configuration_id=other_configuration["id"],
                            revision_id=other_revision["id"])
        foreign_output = self.root / "runs" / "foreign"
        pin_profile(self.store, "other-profile", foreign_output)
        foreign_snapshot = (foreign_output / "profile-start.json").read_bytes()
        managed = self.store.add_configuration("owen", self.configuration_snapshot(runtime="managed"))
        self.store.activate("owen", configuration_id=managed["id"])
        workspace = Workspace(self.root)
        manager = JobManager(workspace)
        self.addCleanup(manager.close)

        class ReplacingModelLease:
            def __init__(self, environment, **kwargs):
                self.environment = dict(environment)
                self.pid = 12345  # Fake model metadata only.

            def start(self):
                return self

            def close(self):
                # A late replacement has a valid hash, but belongs to another
                # identity. Final history must use the already-bound run record.
                target = Path(self.environment["HARNESS2_PROFILE_SNAPSHOT"])
                target.write_bytes(foreign_snapshot)

        phase = {"id": "done", "label": "Deterministic child", "status": "pending",
                 "command": [sys.executable, "-u", "-c", "print('finished')"]}
        with patch.object(workspace, "benchmarks", return_value=[]), \
                patch.object(workspace, "environment", return_value={"PATH": os.environ.get("PATH", ""),
                    "HARNESS2_RUNS_DIR": str(self.root / "runs"), "HARNESS2_UI_PROVIDER": "local"}), \
                patch.object(workspace, "secrets", return_value=[]), \
                patch.object(manager, "_plan", return_value=[phase]), \
                patch("harness2.ui.jobs.ManagedLocalServer", ReplacingModelLease):
            record = manager.start("benchmark", {"profile_id": "owen"}, confirmed=True)
            manager.threads[record["id"]].join(timeout=15)
            self.assertFalse(manager.threads[record["id"]].is_alive())
        final = manager.get(record["id"])
        self.assertEqual(final["status"], "succeeded", final)
        self.assertEqual(self.store.get("other-profile")["runs"], [])
        run = self.store.get("owen")["runs"][0]
        self.assertEqual(run["snapshot_hash"], record["profile"]["snapshot_hash"])
        self.assertEqual(run["configuration_id"], managed["id"])
        self.assertEqual(run["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
