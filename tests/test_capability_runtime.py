# SPDX-License-Identifier: Apache-2.0
"""Catalog exposure boundaries using real archives and context construction.

No inference, runtime executable, network endpoint, or user profile is used. Only
JobBench's task text/source lookup is substituted; archive publication, run
pinning, resume, baseline materialization, and proposer context copies are real.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.capabilities import CapabilityStore
from harness2.capability_runtime import (
    catalog_snapshot,
    declared_sources,
    validate_catalog_snapshot,
)
from harness2.core import context, tree
from harness2.profile_runtime import ProfileAdapter, candidate_from_run, load_snapshot, pin_profile
from harness2.profiles import ProfileStore
from harness2.substrate.opencode import OPENCODE_SURFACE


class CapabilityRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capability context résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profiles = ProfileStore(self.root)
        self.profiles.create("Owen")
        self.configuration = self.profiles.add_configuration("owen", {
            "settings": {"provider": "local", "local_url": "http://127.0.0.1:8087/v1",
                         "local_model": "owen-test", "local_context": "65536",
                         "local_output": "8192", "local_runtime": "external"},
            "identity": {},
        })
        self.baseline = self.profiles.add_revision("owen", {
            "guardrails.md": "Finish local deliverables before optional research.",
        })
        self.profiles.activate("owen", configuration_id=self.configuration["id"],
                               revision_id=self.baseline["id"])
        self.profiles.create("Kestrel")
        self.origin_configuration = self.profiles.add_configuration("kestrel", {
            "settings": {"provider": "local", "local_model": "kestrel-test"}, "identity": {},
        })
        self.profiles.add_note("kestrel", text="ORIGIN_PRIVATE_NOTE_SENTINEL")
        self.component = self.profiles.add_revision("kestrel", {
            "guardrails.md": "CATALOG_COMPONENT_SENTINEL: verify source precedence.",
            "LongTermMEMORY.md": "UNPUBLISHED_FILE_SENTINEL",
        })
        self.profiles.activate("kestrel", configuration_id=self.origin_configuration["id"],
                               revision_id=self.component["id"])
        self.catalog = CapabilityStore(self.root)
        self.output = self.root / "runs" / "experiment"

    def publish(self, **overrides):
        self.catalog.set_enabled(True)
        options = {
            "configuration_id": self.origin_configuration["id"], "name": "Source precedence",
            "paths": ["guardrails.md"], "kind": "guardrail", "portability": "model-sensitive",
            "why": "Conflicting source documents can otherwise cause repeated research.",
            "evidence": [{"run_id": "recorded-run", "path": "summary.json", "sha256": "a" * 64,
                          "qualification": "INTERNAL_EVIDENCE_PROSE_SENTINEL"}],
            "evidence_status": "observed",
        }
        options.update(overrides)
        return self.catalog.publish("kestrel", self.component["id"], **options)

    def pin(self, output=None):
        return pin_profile(self.profiles, "owen", output or self.output)

    def make_context(self, pinned, *, output=None, task="accountants/task1"):
        output = output or self.output
        actual = JobBenchAdapter(store_root=output / "jb" / "dataset")
        wrapped = ProfileAdapter(actual, pinned, output)
        destination = output / "proposer-context"
        with patch.object(actual, "task_prompt", return_value="Produce a concise task deliverable."), \
                patch.object(actual, "task_files", return_value=None):
            built = context.build(destination, wrapped, task, harness_dir=wrapped.base_harness())
        return wrapped, built

    @staticmethod
    def contents(root):
        return "\n".join(path.read_text(encoding="utf-8")
                         for path in root.rglob("*") if path.is_file())

    def assert_absent(self, pinned):
        self.assertNotIn("capabilities", pinned)
        self.assertNotIn("CATALOG_COMPONENT_SENTINEL", (self.output / "profile-start.json").read_text())
        wrapped, built = self.make_context(pinned)
        self.assertFalse(built.library)
        self.assertNotIn("catalog", built.layout.lower())
        self.assertFalse((built.root / "library" / "catalog").exists())
        self.assertNotIn("CATALOG_COMPONENT_SENTINEL", self.contents(built.root))
        self.assertNotIn("CATALOG_COMPONENT_SENTINEL", self.contents(wrapped.base_harness()))

    def test_default_dormant_store_adds_nothing_to_snapshot_or_context(self):
        self.assertFalse(self.catalog.state()["enabled"])
        self.assertIsNone(catalog_snapshot(self.root, "owen"))
        self.assert_absent(self.pin())

    def test_disabled_existing_catalog_is_absent_even_with_profile_consent(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        self.catalog.set_enabled(False)
        self.assertIsNone(catalog_snapshot(self.root, "owen"))
        self.assert_absent(self.pin())

    def test_global_availability_does_not_opt_profile_in(self):
        self.publish()
        self.assertIsNone(catalog_snapshot(self.root, "owen"))
        self.assert_absent(self.pin())

    def test_enabled_opted_in_empty_catalog_adds_no_empty_model_reference(self):
        self.catalog.set_enabled(True)
        self.catalog.set_profile_access("owen", True)
        self.assertIsNone(catalog_snapshot(self.root, "owen"))
        self.assert_absent(self.pin())

    def test_opted_in_pinned_references_reach_proposer_without_changing_solver_baseline(self):
        entry = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        self.assertEqual(pinned["capabilities"]["entries"][0]["id"], entry["id"])
        wrapped, built = self.make_context(pinned)
        self.assertTrue(built.library)
        catalog_root = built.root / "library" / "catalog"
        self.assertTrue((catalog_root / "INDEX.md").is_file())
        texts = self.contents(built.root)
        self.assertIn("CATALOG_COMPONENT_SENTINEL", texts)
        self.assertIn("Source precedence", texts)
        self.assertNotIn("UNPUBLISHED_FILE_SENTINEL", texts)
        self.assertNotIn("ORIGIN_PRIVATE_NOTE_SENTINEL", texts)
        self.assertNotIn("INTERNAL_EVIDENCE_PROSE_SENTINEL", texts)
        self.assertEqual(tree.read_tree(OPENCODE_SURFACE, wrapped.base_harness())["guardrails.md"],
                         self.baseline["tree"]["guardrails.md"])
        self.assertNotIn("CATALOG_COMPONENT_SENTINEL", self.contents(wrapped.base_harness()))
        self.assertEqual(self.profiles.get("owen")["active_revision_id"], self.baseline["id"])
        self.assertEqual(self.profiles.get("kestrel")["active_revision_id"], self.component["id"])

    def test_run_pin_and_resume_keep_exposure_after_global_disable(self):
        entry = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        path = self.output / "profile-start.json"
        before = path.read_bytes()
        self.catalog.set_enabled(False)
        self.catalog.set_profile_access("owen", False)
        self.assertEqual(self.pin(), pinned)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(load_snapshot(path)["capabilities"]["entries"][0]["id"], entry["id"])
        _, built = self.make_context(load_snapshot(path))
        self.assertIn("CATALOG_COMPONENT_SENTINEL", self.contents(built.root))
        later_output = self.root / "runs" / "after-disable"
        later = self.pin(later_output)
        self.assertNotIn("capabilities", later)
        _, later_context = self.make_context(later, output=later_output)
        self.assertNotIn("CATALOG_COMPONENT_SENTINEL", self.contents(later_context.root))

    def test_new_publication_does_not_appear_in_already_pinned_run(self):
        first = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        before = (self.output / "profile-start.json").read_bytes()
        second = self.publish(name="Later publication")
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(self.pin(), pinned)
        self.assertEqual((self.output / "profile-start.json").read_bytes(), before)
        self.assertEqual([entry["id"] for entry in pinned["capabilities"]["entries"]], [first["id"]])
        later = self.pin(self.root / "runs" / "new-experiment")
        self.assertEqual({entry["id"] for entry in later["capabilities"]["entries"]},
                         {first["id"], second["id"]})

    def test_snapshot_hash_detects_modified_reference_file(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        self.pin()
        path = self.output / "profile-start.json"
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["capabilities"]["entries"][0]["files"]["guardrails.md"] = "Modified reference"
        path.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_snapshot(path)

    def test_rehashed_snapshot_still_rejects_reference_content_hash_mismatch(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        self.pin()
        path = self.output / "profile-start.json"
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["capabilities"]["entries"][0]["files"]["guardrails.md"] = "Modified reference"
        edited.pop("snapshot_hash")
        edited["snapshot_hash"] = hashlib.sha256(json.dumps(
            edited, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        path.write_text(json.dumps(edited), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_snapshot(path)

    def test_reference_projection_drops_free_text_but_preserves_public_ids_and_hashes(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        packet = catalog_snapshot(self.root, "owen")
        self.assertEqual(packet["entries"][0]["references"], [{
            "run_id": "recorded-run", "path": "summary.json", "sha256": "a" * 64,
        }])
        self.assertNotIn("INTERNAL_EVIDENCE_PROSE_SENTINEL", json.dumps(packet))

    def test_pinned_packet_validation_rejects_unprojected_reference_fields(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        packet = catalog_snapshot(self.root, "owen")
        for references in (
            [{"run_id": "recorded-run", "raw_report": "PRIVATE_REPORT_BODY"}],
            [{"run_id": {"private_profile": "PRIVATE_PROFILE_BODY"}}],
            ["PRIVATE_FREE_TEXT_REFERENCE"],
            {"raw_report": "PRIVATE_REPORT_BODY"},
            [{"path": "../../outside/summary.json"}],
            [{"path": "judged.json"}],
            [{"path": "rubrics/task.json"}],
            [{"sha256": "not-a-digest"}],
        ):
            with self.subTest(references=references):
                edited = copy.deepcopy(packet)
                edited["entries"][0]["references"] = references
                with self.assertRaises(ValueError):
                    validate_catalog_snapshot(edited)

    def test_metadata_verdict_tripwire_catches_embedded_quoted_json_text(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        packet = catalog_snapshot(self.root, "owen")
        for text in ('{"passed_count":9}', "deterministic_eval_summary"):
            with self.subTest(text=text):
                edited = copy.deepcopy(packet)
                edited["entries"][0]["why"] = text
                with self.assertRaisesRegex(ValueError, "grader-verdict"):
                    validate_catalog_snapshot(edited)

    def test_pinned_packet_validation_rejects_missing_or_nontext_display_names(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        packet = catalog_snapshot(self.root, "owen")
        for name in (None, "", " ", 42, {"raw_report": "PRIVATE_REPORT_BODY"}):
            with self.subTest(name=name):
                edited = copy.deepcopy(packet)
                if name is None:
                    edited["entries"][0].pop("name")
                else:
                    edited["entries"][0]["name"] = name
                with self.assertRaises(ValueError):
                    validate_catalog_snapshot(edited)

    def test_catalog_entry_and_byte_limits_fail_before_any_run_snapshot_is_written(self):
        self.publish()
        self.catalog.set_profile_access("owen", True)
        packet = catalog_snapshot(self.root, "owen")
        oversized = {"version": 1, "entries": [copy.deepcopy(packet["entries"][0]) for _ in range(33)]}
        with self.assertRaisesRegex(ValueError, "entry limit"):
            validate_catalog_snapshot(oversized)
        # Individually bounded strings can still exceed the combined context
        # budget. Detect the total before copying files into a proposer session.
        large = {"version": 1, "entries": []}
        for number in range(9):
            entry = copy.deepcopy(packet["entries"][0])
            entry["id"] = hashlib.sha256(str(number).encode("ascii")).hexdigest()
            entry["description"] = "x" * 131_072
            large["entries"].append(entry)
        with self.assertRaisesRegex(ValueError, "context budget"):
            validate_catalog_snapshot(large)
        with patch("harness2.capability_runtime.MAX_ENTRIES", 0):
            with self.assertRaises(ValueError):
                self.pin()
        self.assertFalse((self.output / "profile-start.json").exists())

    def test_exposure_alone_and_unexposed_source_ids_do_not_establish_borrowing(self):
        entry = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        for notes in ("", f"This catalog contains {entry['id']}", "CAPABILITY_SOURCE: " + "f" * 64):
            with self.subTest(notes=notes):
                self.assertEqual(declared_sources(pinned, notes), [])
        self.assertEqual(declared_sources({}, "CAPABILITY_SOURCE: " + entry["id"]), [])

    def test_explicit_known_source_is_deduplicated_and_remains_an_unvalidated_claim(self):
        entry = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        marker = "CAPABILITY_SOURCE: " + entry["id"]
        claims = declared_sources(pinned, marker + "\n" + marker)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["capability_id"], entry["id"])
        self.assertEqual(claims[0]["origin"], entry["origin"])
        self.assertEqual(claims[0]["evidence_status"], "suspected")
        self.assertEqual(claims[0]["local_validation"], "unvalidated")
        self.assertIn("Model-declared borrowing", claims[0]["qualification"])
        self.assertIn("benefit has not been established", claims[0]["qualification"])

    def test_selected_candidate_archives_declared_origin_after_disable_without_rewriting_source(self):
        entry = self.publish()
        self.catalog.set_profile_access("owen", True)
        pinned = self.pin()
        snapshot_bytes = (self.output / "profile-start.json").read_bytes()
        self.catalog.set_enabled(False)
        archive_before = {path: path.read_bytes() for path in self.catalog.root.rglob("*.json")}
        origin_before = self.profiles.get("kestrel")
        task = self.output / "jb" / "adapt" / "jb-oc-agg1" / "accountants__task1"
        harness = task / "harnesses" / "agg1"
        harness.mkdir(parents=True)
        (harness / "guardrails.md").write_text("A locally modified source-checking candidate.",
                                                encoding="utf-8")
        marker = "CAPABILITY_SOURCE: " + entry["id"]
        record = {"id": "012345abcdef", "title": "Selected candidate fixture", "kind": "benchmark",
                  "status": "succeeded", "output_dir": str(self.output),
                  "params": {"benchmark": "jb", "substrate": "oc"},
                  "profile": {"snapshot_hash": pinned["snapshot_hash"]}}
        # Both final selection notes and the selected proposal's notes can
        # declare ancestry. Notes from an unselected alternative cannot.
        for source in ("selection", "selected-stage", "unselected-stage", "seq-stage"):
            with self.subTest(source=source):
                selected_mode = "seq" if source == "seq-stage" else "agg"
                summary = {"task_id": "accountants/task1", "domain": "accountants",
                           "selections": {selected_mode: {"ok": True, "chosen_label": "agg1",
                               "why": marker if source == "selection" else "Chosen for a local trial."}},
                           "modes": {selected_mode: {"stages": [
                               {"label": "agg1", "notes": marker if source in ("selected-stage", "seq-stage") else ""},
                               {"label": "dom1", "notes": marker if source == "unselected-stage" else ""},
                           ]}}}
                (task / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
                source_before = {path: path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
                arguments = {} if selected_mode == "seq" else {"mode": "agg"}
                candidate = candidate_from_run(self.profiles, "owen", record,
                                               parents=[self.baseline["id"]], **arguments)
                exposed = [ref for ref in candidate["evidence"] if "catalog_exposed_ids" in ref]
                self.assertEqual(exposed[0]["catalog_exposed_ids"], [entry["id"]])
                claims = [ref for ref in candidate["evidence"] if "capability_id" in ref]
                if source == "unselected-stage":
                    self.assertEqual(candidate["borrowed_from"], [])
                    self.assertEqual(claims, [])
                else:
                    self.assertEqual(candidate["borrowed_from"], [{
                        "profile_id": "kestrel", "revision_id": self.component["id"],
                        "component": entry["id"],
                    }])
                    self.assertEqual(claims[0]["origin"], entry["origin"])
                    self.assertEqual(claims[0]["local_validation"], "unvalidated")
                self.assertEqual(candidate["status"], "candidate")
                self.assertEqual(self.profiles.get("owen")["active_revision_id"], self.baseline["id"])
                self.assertEqual({path: path.read_bytes() for path in self.output.rglob("*") if path.is_file()},
                                 source_before)
        self.assertEqual((self.output / "profile-start.json").read_bytes(), snapshot_bytes)
        self.assertEqual(self.profiles.get("kestrel"), origin_before)
        self.assertEqual({path: path.read_bytes() for path in self.catalog.root.rglob("*.json")}, archive_before)


if __name__ == "__main__":
    unittest.main()
