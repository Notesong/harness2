# SPDX-License-Identifier: Apache-2.0
"""Catalog storage, portable components, and real native concurrent publication.

These tests never execute a published helper, start a model, or claim capability
transfer/performance. Strong evidence labels remain publisher declarations.
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.capabilities import CapabilityStore, CorruptCapabilityError
from harness2.core.context import GRADER_VERDICT_MARKERS
from harness2.profiles import ProfileStore


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profiles = ProfileStore(self.root)
        self.profiles.create("Owen")
        self.configuration = self.profiles.add_configuration(
            "owen", {"settings": {"provider": "local"}, "identity": {"model": "Example"}})
        self.revision = self.profiles.add_revision("owen", {"guardrails.md": "Write progress early."})
        self.store = CapabilityStore(self.root)

    def publish(self, **kwargs):
        options = {"configuration_id": self.configuration["id"], "name": "Progress checkpointing",
                   "paths": ["guardrails.md"], "kind": "guardrail", "portability": "model-sensitive"}
        options.update(kwargs)
        return self.store.publish("owen", self.revision["id"], **options)

    def test_disabled_by_default_has_no_published_objects_and_rejects_publication(self):
        state = self.store.state()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["entries"], [])
        self.assertEqual(state["profile_access"], {})
        with self.assertRaisesRegex(ValueError, "Enable"):
            self.publish()
        self.assertEqual(list(self.store.root.rglob("*.json")), [])
        self.assertEqual(self.store.state(), state)

    def test_global_enable_and_individual_consent_are_separate_and_survive_disable(self):
        self.store.set_profile_access("owen", True)
        self.assertFalse(self.store.state()["effective_access"]["owen"])
        self.store.set_enabled(True)
        entry = self.publish()
        enabled = self.store.state()
        self.assertTrue(enabled["effective_access"]["owen"])
        self.store.set_enabled(False)
        state = self.store.state()
        self.assertTrue(state["profile_access"]["owen"])
        self.assertFalse(state["effective_access"]["owen"])
        self.assertEqual(self.store.get(entry["id"])["files"], entry["files"])
        self.assertEqual(len(state["entries"]), 1)
        self.store.set_enabled(True)
        self.assertTrue(self.store.state()["effective_access"]["owen"])
        self.profiles.create("Another model")
        self.assertNotIn("another-model", self.store.state()["profile_access"])
        with self.assertRaises(ValueError):
            self.store.set_profile_access("nonexistent", True)
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                self.store.set_enabled(value)
            with self.assertRaises(ValueError):
                self.store.set_profile_access("owen", value)

    def test_model_references_require_both_gates_and_are_fresh_coherent_snapshots(self):
        self.assertIsNone(self.store.references_for("owen"))
        self.store.set_profile_access("owen", True)
        self.assertIsNone(self.store.references_for("owen"))
        self.store.set_enabled(True)
        self.assertEqual(self.store.references_for("owen"), [])
        entry = self.publish()
        snapshot = self.store.references_for("owen")
        self.assertEqual([item["id"] for item in snapshot], [entry["id"]])
        snapshot[0]["files"]["guardrails.md"] = "Only a mutated caller copy."
        self.assertEqual(self.store.references_for("owen")[0]["files"], entry["files"])
        self.store.set_profile_access("owen", False)
        self.assertIsNone(self.store.references_for("owen"))
        self.store.set_profile_access("owen", True)
        self.store.set_enabled(False)
        self.assertIsNone(self.store.references_for("owen"))
        self.assertEqual(self.store.get(entry["id"])["files"], entry["files"])

    def test_packet_is_immutable_separates_evidence_and_never_changes_active_harness(self):
        self.store.set_enabled(True)
        self.profiles.activate("owen", configuration_id=self.configuration["id"],
                               revision_id=self.revision["id"])
        original = self.profiles.get("owen")
        entry = self.publish(evidence=[{"path": "runs/one", "status": "completed"}],
                             evidence_status="observed", costs={"context_tokens": 18},
                             incompatibilities=["May be distracting for another model."],
                             failures=["Not evaluated after a second compaction."],
                             motivated_by=["Conversation / progress discussion"])
        self.assertEqual(entry["origin"], {
            "profile_id": "owen", "profile_name": "Owen",
            "configuration_id": self.configuration["id"], "revision_id": self.revision["id"]})
        self.assertEqual(entry["evidence_status"], "observed")
        self.assertIn("not independently verified", entry["evidence_qualification"])
        self.assertIn("does not prove", entry["configuration_association"])
        self.assertEqual(entry["ancestry"], "declared-independent")
        self.assertIn("not certified", entry["invention_qualification"])
        self.assertTrue(entry["created_at"])
        self.assertEqual(self.profiles.get("owen"), original)
        before = {path: path.read_bytes() for path in self.store.root.rglob("*.json")}
        entry["files"]["guardrails.md"] = "Changed locally"
        entry["costs"]["context_tokens"] = 10_000
        self.assertEqual(self.store.get(entry["id"])["files"]["guardrails.md"], "Write progress early.")
        self.assertEqual(self.store.get(entry["id"])["costs"]["context_tokens"], 18)
        self.publish(name="Another proposed capability")
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        summary = self.store.state()["entries"][0]
        self.assertNotIn("files", summary)
        self.assertEqual(summary["paths"], ["guardrails.md"])
        self.assertEqual(summary["file_count"], 1)

    def test_origin_must_exist_but_need_not_be_the_current_active_selection(self):
        self.store.set_enabled(True)
        other_configuration = self.profiles.add_configuration(
            "owen", {"settings": {"provider": "local", "local_context": "8192"}, "identity": {}})
        self.profiles.activate("owen", configuration_id=other_configuration["id"])
        self.assertEqual(self.publish()["origin"]["configuration_id"], self.configuration["id"])
        self.profiles.create("Another")
        foreign = self.profiles.add_configuration("another", {"settings": {}, "identity": {}})
        with self.assertRaises(ValueError):
            self.publish(configuration_id=foreign["id"])
        with self.assertRaises(ValueError):
            self.store.publish("owen", "f" * 64, configuration_id=self.configuration["id"],
                               name="Missing", paths=["guardrails.md"], kind="other", portability="unknown")

    def test_skill_selection_is_explicit_and_dependencies_are_not_silently_copied(self):
        self.store.set_enabled(True)
        marker = self.root / "must-not-execute.txt"
        skill = "---\nname: checkpoint\ndescription: checkpoint work\n---\npython scripts/helper.py\n"
        revision = self.profiles.add_revision("owen", {
            "guardrails.md": "Load checkpoint when needed.",
            "skills/checkpoint/SKILL.md": skill,
            "scripts/helper.py": f"open({str(marker)!r}, 'w').write('ran')\n",
        })
        kwargs = {"configuration_id": self.configuration["id"], "name": "Checkpoint skill",
                  "kind": "skill", "portability": "deterministic"}
        with self.assertRaisesRegex(ValueError, "not in the harness"):
            self.store.publish("owen", revision["id"], paths=["skills/checkpoint/SKILL.md"], **kwargs)
        entry = self.store.publish("owen", revision["id"],
            paths=["skills/checkpoint/SKILL.md", "scripts/helper.py"], **kwargs)
        self.assertEqual(set(entry["files"]), {"skills/checkpoint/SKILL.md", "scripts/helper.py"})
        self.assertNotIn("guardrails.md", entry["files"])
        self.assertFalse(marker.exists())

    def test_duplicate_publication_is_idempotent_and_content_hash_is_independent_of_claims(self):
        self.store.set_enabled(True)
        first = self.publish()
        before = self.store.state()
        second = self.publish()
        self.assertEqual(first, second)
        self.assertEqual(self.store.state(), before)
        another_claim = self.publish(description="Another hypothesis about identical files.")
        self.assertNotEqual(first["id"], another_claim["id"])
        self.assertEqual(first["content_hash"], another_claim["content_hash"])

    def test_derived_ancestry_is_explicit_and_survives_republication_from_fork_evidence(self):
        self.store.set_enabled(True)
        parent = self.publish()
        self.profiles.create("Another")
        configuration = self.profiles.add_configuration("another", {"settings": {}, "identity": {}})
        revision = self.profiles.add_revision("another", {"guardrails.md": "Checkpoint, then verify."},
            evidence=[{"capability_id": parent["id"], "local_validation": "unvalidated"}],
            operation="capability-fork")
        child = self.store.publish("another", revision["id"], configuration_id=configuration["id"],
            name="A modified checkpoint", kind="guardrail", portability="model-sensitive",
            paths=["guardrails.md"])
        self.assertEqual(child["derived_from"], [parent["id"]])
        self.assertEqual(child["ancestry"], "derived")
        self.assertEqual(child["evidence_status"], "suspected")
        self.assertEqual(self.store.get(parent["id"])["files"], parent["files"])
        with self.assertRaises(ValueError):
            self.publish(derived_from=["f" * 64])

    def test_bad_metadata_paths_and_claims_never_publish(self):
        self.store.set_enabled(True)
        before = self.store.state()
        for options in (
            {"kind": "executable"}, {"portability": "universal"}, {"name": " "},
            {"paths": []}, {"paths": ["../guardrails.md"]}, {"paths": ["guardrails.md:secret"]},
            {"paths": ["guardrails.md", "guardrails.md"]}, {"paths": ["secret.env"]},
            {"paths": ["scripts/not_selected.py"]}, {"evidence_status": "verified"},
            {"evidence_status": "replicated"},
            {"evidence_status": "replicated", "evidence": [{"claim": "It is proven."}]},
            {"costs": [1]},
            {"costs": {"api_key": "private"}},
            {"evidence": [{"url": "http://user:password@localhost/"}]},
            {"evidence": [{"url": "https://example.org/?secret=private"}]},
            *({"why": marker} for marker in GRADER_VERDICT_MARKERS),
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.publish(**options)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(list((self.store.root / "objects").glob("*.json")), [])

    def test_fork_conflicts_need_explicit_consent_and_modifications_stay_candidates(self):
        self.store.set_enabled(True)
        entry = self.publish()
        self.profiles.create("Another")
        baseline = self.profiles.add_revision("another", {"guardrails.md": "An independent baseline."},
            experience={"lesson": "Keep the local model's own evidence."}, domain="technical-writers")
        self.profiles.activate("another", revision_id=baseline["id"])
        source_before, target_before = self.profiles.get("owen"), self.profiles.get("another")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.store.fork(entry["id"], "another")
        self.assertEqual(self.profiles.get("another"), target_before)
        candidate = self.store.fork(entry["id"], "another", why="A modified hypothesis to compare.",
            replace_paths=["guardrails.md"], replace_files={"guardrails.md": "Checkpoint only after edits."})
        self.assertEqual(candidate["tree"]["guardrails.md"], "Checkpoint only after edits.")
        self.assertEqual(candidate["parents"], [baseline["id"]])
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["domain"], baseline["domain"])
        self.assertEqual(candidate["experience"], baseline["experience"])
        self.assertEqual(candidate["borrowed_from"][0]["component"], entry["id"])
        self.assertEqual(candidate["evidence"][-1]["local_validation"], "unvalidated")
        self.assertEqual(candidate["evidence"][-1]["modified_files"], ["guardrails.md"])
        self.assertEqual(self.profiles.get("another")["active_revision_id"], baseline["id"])
        self.assertEqual(self.profiles.get("owen"), source_before)
        self.assertEqual(self.store.get(entry["id"]), entry)
        for options in ({"replace_files": {"scripts/additional.py": "pass\n"}},
                        {"replace_paths": ["scripts/additional.py"]},
                        {"parent_revision_id": self.revision["id"]},
                        {"replace_paths": ["guardrails.md"], "replace_files": {"guardrails.md": GRADER_VERDICT_MARKERS[0]}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.store.fork(entry["id"], "another", **options)
        self.store.set_enabled(False)
        before = self.profiles.get("another")
        with self.assertRaisesRegex(ValueError, "Enable"):
            self.store.fork(entry["id"], "another", replace_paths=["guardrails.md"])
        self.assertEqual(self.profiles.get("another"), before)

    def test_standalone_skill_fork_records_discovery_changes_without_adoption(self):
        self.store.set_enabled(True)
        skill = "---\nname: checkpoint\ndescription: checkpoint work\n---\nRecord progress.\n"
        source = self.profiles.add_revision("owen", {
            "guardrails.md": "Load checkpoint when needed.", "skills/checkpoint/SKILL.md": skill})
        entry = self.store.publish("owen", source["id"], configuration_id=self.configuration["id"],
            name="A standalone skill", kind="skill", portability="model-sensitive",
            paths=["skills/checkpoint/SKILL.md"])
        self.profiles.create("Another")
        baseline = self.profiles.add_revision("another", {"guardrails.md": "Finish the task."})
        self.profiles.activate("another", revision_id=baseline["id"])
        candidate = self.store.fork(entry["id"], "another")
        self.assertIn("checkpoint", candidate["tree"]["guardrails.md"])
        self.assertEqual(candidate["tree"]["skills/checkpoint/SKILL.md"], skill)
        self.assertEqual(candidate["evidence"][-1]["repaired_skills"], ["checkpoint"])
        self.assertEqual(candidate["evidence"][-1]["integration_changes"][0]["path"], "guardrails.md")
        self.assertIn("guardrails.md", candidate["evidence"][-1]["modified_files"])
        self.assertEqual(self.profiles.get("another")["active_revision_id"], baseline["id"])
        self.assertEqual(self.store.get(entry["id"])["files"], {"skills/checkpoint/SKILL.md": skill})

    def test_later_and_foreign_revision_ancestry_cannot_drop_declared_catalog_origin(self):
        self.store.set_enabled(True)
        entry = self.publish()
        fork = self.store.fork(entry["id"], "owen", parent_revision_id=self.revision["id"])
        later = self.profiles.add_revision("owen", {"guardrails.md": "A later variation."},
            parents=[fork["id"]], evidence=[{"run_id": "later-task"}])
        self.profiles.create("Another")
        configuration = self.profiles.add_configuration("another", {"settings": {}, "identity": {}})
        imported = self.profiles.add_revision("another", later["tree"], borrowed_from=[{
            "profile_id": "owen", "revision_id": later["id"], "component": "guardrails.md"}])
        descendant = self.store.publish("another", imported["id"], configuration_id=configuration["id"],
            name="A transmitted later variation", kind="guardrail", portability="model-sensitive",
            paths=["guardrails.md"])
        self.assertEqual(descendant["derived_from"], [entry["id"]])
        self.assertEqual(descendant["ancestry"], "derived")

    def test_interrupted_publication_leaves_invisible_object_and_can_retry(self):
        self.store.set_enabled(True)
        before = self.store.state()
        with patch.object(self.store._archive, "_append", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.store.state(), before)
        self.assertEqual(len(list((self.store.root / "objects").glob("*.json"))), 1)
        self.publish()
        self.assertEqual(len(self.store.state()["entries"]), 1)

    def test_object_corruption_and_a_missing_event_are_detected(self):
        self.store.set_enabled(True)
        entry = self.publish()
        object_path = self.store.root / "objects" / f"{entry['id']}.json"
        original = object_path.read_bytes()
        payload = json.loads(original)
        payload["data"]["files"]["guardrails.md"] = "Corrupted"
        object_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(CorruptCapabilityError):
            self.store.get(entry["id"])
        object_path.write_bytes(original)
        self.store.set_enabled(False)
        sorted((self.store.root / "events").glob("*.json"))[1].unlink()
        with self.assertRaises(CorruptCapabilityError):
            self.store.state()

    def test_threads_and_native_process_writers_preserve_every_event(self):
        self.store.set_enabled(True)
        def publish_thread(index):
            store = CapabilityStore(self.root)
            store.publish("owen", self.revision["id"], configuration_id=self.configuration["id"],
                name=f"Thread {index}", paths=["guardrails.md"], kind="guardrail", portability="unknown")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(publish_thread, range(8)))
        script = (
            "from harness2.capabilities import CapabilityStore; import sys; "
            "s=CapabilityStore(sys.argv[1]); "
            "[s.publish('owen',sys.argv[2],configuration_id=sys.argv[3],"
            "name='Process '+sys.argv[4]+' / '+str(i),paths=['guardrails.md'],"
            "kind='guardrail',portability='unknown') for i in range(3)]"
        )
        children = [subprocess.Popen([sys.executable, "-c", script, str(self.root), self.revision["id"],
                                      self.configuration["id"], str(i)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
        self.addCleanup(lambda: [child.kill() for child in children if child.poll() is None])
        for child in children:
            _, error = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
        view = self.store.state()
        self.assertEqual(len(view["entries"]), 17)
        self.assertEqual([event["sequence"] for event in view["events"]], list(range(1, 19)))
        self.assertEqual(len({entry["content_hash"] for entry in view["entries"]}), 1)

    def test_disabled_native_process_cannot_publish_or_restore_itself(self):
        self.store.set_enabled(True)
        self.publish()
        self.store.set_enabled(False)
        before = {path: path.read_bytes() for path in self.store.root.rglob("*.json")}
        script = (
            "from harness2.capabilities import CapabilityStore; import sys; "
            "s=CapabilityStore(sys.argv[1]); "
            "s.publish('owen',sys.argv[2],configuration_id=sys.argv[3],name='Forbidden',"
            "paths=['guardrails.md'],kind='guardrail',portability='unknown')"
        )
        result = subprocess.run([sys.executable, "-c", script, str(self.root), self.revision["id"],
                                 self.configuration["id"]], capture_output=True, check=False, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Enable the capability catalog", result.stderr)
        self.assertFalse(self.store.state()["enabled"])
        self.assertEqual({path: path.read_bytes() for path in self.store.root.rglob("*.json")}, before)

    def test_storage_rejects_symlinks_or_windows_junctions(self):
        self.store.set_enabled(True)
        outside = self.root / "outside"
        outside.mkdir()
        link = self.store.root / "objects"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True, check=False)
            if result.returncode:
                self.skipTest("Windows junction creation unavailable.")
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.store.state()["entries"], [])


if __name__ == "__main__":
    unittest.main()
