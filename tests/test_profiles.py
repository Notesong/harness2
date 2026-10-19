# SPDX-License-Identifier: Apache-2.0
"""Archive integrity and native concurrent writers; no model calls are made."""

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.profiles import CorruptProfileError, ProfileStore


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ProfileStore(self.root)
        self.store.create("Owen", "A continuing identity.")

    def configuration(self, context=65536):
        return self.store.add_configuration("owen", {
            "settings": {"provider": "local", "local_context": str(context)},
            "identity": {"weights_sha256": "a" * 64, "reasoning": False,
                         "roles": {"solver": "Qwen", "judge": "Qwen"}},
        }, label=f"{context} context")

    def revision(self, text="Finish work before rereading.", **kwargs):
        return self.store.add_revision("owen", {"guardrails.md": text}, **kwargs)

    def test_configuration_changes_preserve_identity_and_original_snapshots(self):
        first = self.configuration(32768)
        self.store.activate("owen", configuration_id=first["id"])
        second = self.configuration(65536)
        self.store.activate("owen", configuration_id=second["id"], why="A new bounded comparison.")
        view = self.store.get("owen")
        self.assertEqual(view["id"], "owen")
        self.assertEqual(view["active_configuration_id"], second["id"])
        self.assertEqual(view["configurations"][0]["snapshot"]["settings"]["local_context"], "32768")
        first["snapshot"]["settings"]["local_context"] = "broken"
        self.assertEqual(self.store.get("owen")["configurations"][0]["snapshot"]["settings"]
                         ["local_context"], "32768")
        self.assertEqual(self.store.list()[0]["configurations_count"], 2)

    def test_graph_branches_rollbacks_and_simplification_keep_old_bytes(self):
        baseline = self.revision(label="baseline")
        expanded_tree = {"guardrails.md": "Finish work before rereading.", "scripts/check.py": "pass\n"}
        expanded = self.store.add_revision("owen", expanded_tree, parents=[baseline["id"]], label="A")
        before = {p: p.read_bytes() for p in self.store.root.rglob("*.json")}
        branch = self.revision("Write progress early.", parents=[baseline["id"]], label="B")
        simplified = self.revision(parents=[expanded["id"], branch["id"]], operation="simplify",
                                   why="Remove a helper without losing its tested behavior.",
                                   motivated_by=["conversation / progress-state discussion"])
        self.store.activate("owen", revision_id=simplified["id"])
        self.store.retire("owen", simplified["id"], why="Not yet supported on another task.")
        self.assertIsNone(self.store.get("owen")["active_revision_id"])
        self.store.activate("owen", revision_id=baseline["id"], why="Return to the baseline.")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        view = self.store.get("owen")
        self.assertEqual(len(view["revisions"]), 4)
        self.assertNotIn("scripts/check.py", simplified["tree"])
        self.assertIn("scripts/check.py", expanded["tree"])
        self.assertEqual(view["active_revision_id"], baseline["id"])
        self.assertEqual(view["revisions"][-1]["status"], "retired")
        self.store.activate("owen", revision_id=simplified["id"], why="New evidence warrants retry.")
        self.assertFalse(self.store.get("owen")["revisions"][-1]["retired"])

    def test_foreign_borrowing_is_explicit_and_does_not_change_either_active_tree(self):
        source = self.revision(label="Owen's candidate")
        self.store.activate("owen", revision_id=source["id"])
        self.store.create("Another model", profile_id="another")
        borrowed = self.store.add_revision("another", source["tree"], operation="import",
            borrowed_from=[{"profile_id": "owen", "revision_id": source["id"],
                            "component": "guardrails.md"}], why="A hypothesis to test.")
        self.assertIsNone(self.store.get("another")["active_revision_id"])
        self.assertEqual(self.store.get("owen")["active_revision_id"], source["id"])
        self.assertEqual(borrowed["borrowed_from"][0]["revision_id"], source["id"])
        with self.assertRaises(ValueError):
            self.store.add_revision("another", {}, parents=[source["id"]])

    def test_evidence_status_is_independent_of_adoption(self):
        revision = self.revision()
        self.store.activate("owen", revision_id=revision["id"])
        for status in ("observed", "suspected", "replicated", "transferred"):
            self.store.add_note("owen", text="A separately qualified observation.",
                evidence_status=status, references=[{"path": "runs/example/summary.json", "sha256": "a" * 64}])
        self.assertEqual(len(self.store.get("owen")["notes"]), 4)
        with self.assertRaises(ValueError):
            self.store.add_note("owen", text="Adoption does not imply proven benefit.", evidence_status="adopted")
        self.store.record_run("owen", {"path": "runs/example", "revision_id": revision["id"],
                                       "status": "completed", "baseline_graded": False})
        self.assertFalse(self.store.get("owen")["runs"][0]["baseline_graded"])

    def test_experience_is_a_frozen_single_entry_with_no_verdict_leak(self):
        experience = {"cue": "Technical writing", "lesson": "Write deliverables earlier.",
                      "evidence": "A partial probe.", "confidence": "suspected", "edits": "A smaller plan."}
        revision = self.revision(experience=experience)
        experience["lesson"] = "Mutated"
        self.assertEqual(revision["experience"]["lesson"], "Write deliverables earlier.")
        with self.assertRaises(ValueError):
            self.revision(experience={"arbitrary": "Not an experience field."})
        from harness2.core.context import GRADER_VERDICT_MARKERS
        with self.assertRaises(ValueError):
            self.revision(experience={"lesson": GRADER_VERDICT_MARKERS[0]})

    def test_repeated_identical_objects_are_idempotent(self):
        first = self.configuration()
        second = self.configuration()
        baseline = self.revision()
        repeated = self.revision()
        self.assertEqual(first, second)
        self.assertEqual(baseline, repeated)
        self.assertEqual(len(self.store.get("owen")["events"]), 3)

    def test_run_updates_project_latest_reference_without_rewriting_history(self):
        self.store.record_run("owen", {"job_id": "job-one", "output_dir": "runs/one",
                                       "status": "running", "configuration_id": "a" * 64})
        before = {p: p.read_bytes() for p in self.store.root.rglob("*.json")}
        self.store.record_run("owen", {"job_id": "job-one", "output_dir": "runs/one", "status": "completed"})
        self.store.record_run("owen", {"output_dir": "runs/imported", "status": "running"})
        self.store.record_run("owen", {"output_dir": "runs/imported", "status": "failed"})
        view = self.store.get("owen")
        self.assertEqual(len(view["runs"]), 2)
        self.assertEqual([r["status"] for r in view["runs"]], ["completed", "failed"])
        self.assertEqual(view["runs"][0]["configuration_id"], "a" * 64)
        self.assertEqual(len([e for e in view["events"] if e["action"] == "run_recorded"]), 4)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_interrupted_publication_leaves_history_unchanged_and_can_retry(self):
        original = self.store.get("owen")
        with patch.object(self.store, "_append", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.configuration()
        self.assertEqual(self.store.get("owen"), original)
        self.assertEqual(len(list((self.store.root / "owen" / "objects").glob("*.json"))), 1)
        self.configuration()
        self.assertEqual(len(self.store.get("owen")["configurations"]), 1)

    def test_interrupted_creation_does_not_reserve_identity(self):
        with patch.object(self.store, "_append", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.store.create("Next model")
        self.assertEqual([p["id"] for p in self.store.list()], ["owen"])
        self.assertEqual(self.store.create("Next model")["id"], "next-model")

    def test_corrupt_object_and_missing_middle_event_are_detected(self):
        configuration = self.configuration()
        self.revision()
        object_path = self.store.root / "owen" / "objects" / f"{configuration['id']}.json"
        original = object_path.read_bytes()
        payload = json.loads(original)
        payload["data"]["label"] = "Altered"
        object_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CorruptProfileError):
            self.store.get("owen")
        object_path.write_bytes(original)
        event_path = sorted((self.store.root / "owen" / "events").glob("*.json"))[1]
        event_path.unlink()
        with self.assertRaises(CorruptProfileError):
            self.store.get("owen")

    def test_bad_paths_secret_fields_and_oversize_trees_do_not_append_events(self):
        before = self.store.get("owen")["events"]
        for bad in ("../escape", "UPPER", "con", "ends.", "a/b"):
            with self.subTest(profile_id=bad), self.assertRaises(ValueError):
                self.store.create("Bad", profile_id=bad)
        for tree in ({"../guardrails.md": "Bad"}, {"secret.env": "Bad"},
                     {"guardrails.md": "x", "GUARDRAILS.md": "y"},
                     {"guardrails.md": "x" * 24_001}):
            with self.subTest(tree=list(tree)), self.assertRaises(ValueError):
                self.store.add_revision("owen", tree)
        for snapshot in (
            {"settings": {"api_key": "private"}, "identity": {}},
            {"settings": {}, "identity": {"authorization": "private"}},
            {"settings": {"local_url": "http://user:password@localhost:8080/v1"}, "identity": {}},
            {"settings": {"local_url": "http://localhost/v1?key=private"}, "identity": {}},
        ):
            with self.assertRaises(ValueError):
                self.store.add_configuration("owen", snapshot)
        self.assertEqual(self.store.get("owen")["events"], before)

    def test_concurrent_threads_and_processes_do_not_lose_events(self):
        def write_thread(index):
            ProfileStore(self.root).add_note("owen", text=f"Thread {index}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(write_thread, range(15)))
        script = (
            "from harness2.profiles import ProfileStore; import sys; "
            "s=ProfileStore(sys.argv[1]); "
            "[s.add_note('owen',text='Process '+sys.argv[2]+' / '+str(i)) for i in range(5)]"
        )
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.root), str(i)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
        self.addCleanup(lambda: [p.kill() for p in children if p.poll() is None])
        for child in children:
            _, error = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
        view = self.store.get("owen")
        self.assertEqual(len(view["notes"]), 30)
        self.assertEqual(len({n["text"] for n in view["notes"]}), 30)
        self.assertEqual([e["sequence"] for e in view["events"]], list(range(1, 32)))

    def test_symlink_or_windows_junction_cannot_redirect_profile_storage(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.store.root / "escaped"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True, check=False)
            if result.returncode:
                self.skipTest("Windows junction creation unavailable.")
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.get("escaped")
        with self.assertRaises(ValueError):
            self.store.list()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
