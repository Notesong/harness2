# SPDX-License-Identifier: Apache-2.0
"""Configuration byte identity and immutable historical import, without inference."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2 import profile_import as importer
from harness2.profiles import ProfileStore
from harness2.ui.workspace import FIELDS


class ConfigurationCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="profile configuration résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.weights = self.root / "model-UD-Q3_K_XL.gguf"
        self.weights.write_bytes(b"fixture weights, not an actual model\0\xff")
        self.runtime = self.root / "llama-server.exe"
        self.runtime.write_bytes(b"fixture executable, never executed")
        self.environment = {
            "HARNESS2_UI_PROVIDER": "local", "HARNESS2_LOCAL_RUNTIME": "managed",
            "HARNESS2_LOCAL_MODEL": "fixture-local-64k",
            "HARNESS2_LOCAL_BASE_URL": "http://127.0.0.1:8087/v1/",
            "HARNESS2_LOCAL_CONTEXT": "65536", "HARNESS2_LOCAL_OUTPUT": "8192",
            "HARNESS2_LOCAL_WEIGHTS": str(self.weights),
            "HARNESS2_LOCAL_EXECUTABLE": str(self.runtime),
            "HARNESS2_LOCAL_API_KEY": "private-fixture-key", "UNRELATED_SETTING": "discard-me",
            "OPENCODE_CONFIG_CONTENT": '{"provider":{"local":{"apiKey":"private-provider"}}}',
        }

    def test_exact_file_hashes_roles_and_budgets_without_running_a_program(self):
        with patch("subprocess.run", side_effect=AssertionError("No runtime inspection")):
            captured = importer.capture_configuration(self.environment)
        identity = captured["identity"]
        for name, path in (("weights", self.weights), ("runtime", self.runtime)):
            self.assertEqual(identity[name]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(identity[name]["size_bytes"], path.stat().st_size)
            self.assertEqual(identity[name]["status"], "hashed")
        self.assertEqual(identity["budgets"], {"context": 65536, "output": 8192})
        self.assertEqual(identity["roles"], {"solver": "local/fixture-local-64k",
                                            "improver": "local/fixture-local-64k",
                                            "judge": "fixture-local-64k"})
        self.assertEqual(identity["reasoning"]["mode"], "off")
        self.assertIsNone(identity["runtime"]["version"])
        self.assertEqual(identity["weights"]["annotation"]["quantization_label"], "UD-Q3_K_XL")
        self.assertIn("filename annotation only", identity["weights"]["annotation"]["source"])
        self.assertEqual(captured["settings"]["local_url"], "http://127.0.0.1:8087/v1")
        encoded = json.dumps(captured)
        for value in ("private-fixture-key", "private-provider", "discard-me", "OPENCODE_CONFIG"):
            self.assertNotIn(value, encoded)
        self.assertEqual(set(captured), {"settings", "identity"})
        self.assertEqual(importer.SETTING_ENV, FIELDS)

    def test_external_cloud_identity_remains_unknown_and_explicit_roles_survive(self):
        captured = importer.capture_configuration({
            "HARNESS2_UI_PROVIDER": "vertex", "HARNESS2_SOLVER_MODEL": "google-vertex/one",
            "HARNESS2_IMPROVER_MODEL": "google-vertex/two", "HARNESS2_JUDGE_MODEL": "three",
            "GOOGLE_CLOUD_PROJECT": "fixture-project",
        })
        self.assertEqual(captured["settings"]["provider"], "vertex")
        self.assertEqual(captured["identity"]["roles"]["improver"], "google-vertex/two")
        self.assertIsNone(captured["identity"]["weights"]["sha256"])
        self.assertIsNone(captured["identity"]["runtime"]["version"])
        self.assertIsNone(captured["identity"]["reasoning"]["mode"])
        self.assertIsNone(captured["identity"]["tools"]["policy"])

    def test_file_mutation_during_hash_does_not_publish_a_false_fingerprint(self):
        before = self.weights.stat()
        after = SimpleNamespace(**{key: getattr(before, key) for key in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode"
        )})
        after.st_mtime_ns += 1
        with patch.object(importer.os, "fstat", side_effect=[before, after]):
            value = importer._fingerprint(str(self.weights))
        self.assertEqual(value["status"], "unknown")
        self.assertIsNone(value["sha256"])
        self.assertIn("changed", value["reason"])

    def test_windows_path_and_handle_ctime_can_differ_without_hiding_a_change(self):
        values = {"st_dev": 1, "st_ino": 2, "st_size": 3, "st_mtime_ns": 4}
        path = SimpleNamespace(**values, st_ctime_ns=100)
        handle = SimpleNamespace(**values, st_ctime_ns=200)
        self.assertTrue(importer._unchanged_file(path, handle, handle, path))
        changed = SimpleNamespace(**values, st_ctime_ns=201)
        self.assertFalse(importer._unchanged_file(path, handle, changed, path))
        replacement = SimpleNamespace(**{**values, "st_ino": 99}, st_ctime_ns=100)
        self.assertFalse(importer._unchanged_file(path, handle, handle, replacement))

    def test_invalid_url_and_misplaced_authentication_are_rejected_without_echo(self):
        for change in ({"HARNESS2_LOCAL_BASE_URL": "http://user:private-fixture-key@127.0.0.1/v1"},
                       {"HARNESS2_LOCAL_MODEL": "private-fixture-key"}):
            with self.subTest(change=next(iter(change))):
                with self.assertRaises(ValueError) as caught:
                    importer.capture_configuration({**self.environment, **change})
                self.assertNotIn("private-fixture-key", str(caught.exception))

    def test_missing_files_are_unknown_and_replaced_bytes_change_identity(self):
        first = importer.capture_configuration(self.environment)
        self.weights.write_bytes(b"different bytes")
        second = importer.capture_configuration(self.environment)
        self.assertNotEqual(first["identity"]["weights"]["sha256"],
                            second["identity"]["weights"]["sha256"])
        self.runtime.unlink()
        third = importer.capture_configuration(self.environment)
        self.assertEqual(third["identity"]["runtime"]["status"], "unknown")

    def test_tool_policy_tracks_changes_but_credential_rotation_does_not_change_it(self):
        base = {"tools": {"webfetch": True, "bash": False},
                "permission": {"bash": "ask", "webfetch": "allow"},
                "provider": {"local": {"options": {"apiKey": "first-private-key",
                    "baseURL": "http://user:first-private-key@127.0.0.1:8087/v1?auth=secret"}}}}
        environment = {"OPENCODE_CONFIG_CONTENT": json.dumps(base)}
        first = importer.tool_policy_fingerprint(environment)
        base["provider"]["local"]["options"]["apiKey"] = "second-private-key"
        base["provider"]["local"]["options"]["baseURL"] = (
            "http://user:second-private-key@127.0.0.1:8087/v1?auth=changed")
        second = importer.tool_policy_fingerprint({"OPENCODE_CONFIG_CONTENT": json.dumps(base)})
        self.assertEqual(first, second)
        self.assertEqual(first["policy"], {"tools": {"webfetch": True, "bash": False},
                                           "permission": {"bash": "ask", "webfetch": "allow"}})
        encoded = json.dumps(first)
        self.assertNotIn("private-key", encoded)
        self.assertNotIn("auth=", encoded)
        base["tools"]["webfetch"] = False
        third = importer.tool_policy_fingerprint({"OPENCODE_CONFIG_CONTENT": json.dumps(base)})
        self.assertNotEqual(first["declared_configuration_sha256"],
                            third["declared_configuration_sha256"])

    def test_tool_policy_declared_file_is_bounded_and_not_modified(self):
        path = self.root / "opencode.json"
        original = b'{"tools":{"webfetch":false},"apiKey":"private-file-key"}'
        path.write_bytes(original)
        policy = importer.tool_policy_fingerprint({"OPENCODE_CONFIG": str(path)})
        self.assertEqual(policy["configuration_file"]["status"], "captured")
        self.assertEqual(policy["policy"]["tools"], {"webfetch": False})
        self.assertNotIn("private-file-key", json.dumps(policy))
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(b"x" * (128 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "could not be captured"):
            importer.tool_policy_fingerprint({"OPENCODE_CONFIG": str(path)})

    def test_invalid_policy_errors_do_not_echo_private_json(self):
        with self.assertRaises(ValueError) as caught:
            importer.tool_policy_fingerprint({"OPENCODE_CONFIG_CONTENT": "{private-json:"})
        self.assertNotIn("private-json", str(caught.exception))


class HistoricalImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Owen archive résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / importer.OWEN_RUN
        self.store = ProfileStore(self.root)
        self.summary_path = self.run / importer.SUMMARY
        self.harness_root = self.summary_path.parent / "harnesses"
        self.baseline = {"systemprompt.md": "<!-- original -->\n", "guardrails.md": "",
                         "LongTermMEMORY.md": "", "tool_descriptions/tool_guidance.md": ""}
        self.domain = {**self.baseline, "guardrails.md": "Re-open every output before completion.\n",
                       "LongTermMEMORY.md": "Use authoritative sources.\n"}
        for label, tree in (("base", self.baseline), ("dom1", self.domain), ("agg1", self.domain)):
            for relative, text in {**tree, "scripts/README.md": "<!-- stub -->\n"}.items():
                path = self.harness_root / label / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))
        self.manifest = {
            "executable": "C:/saved/llama-server.exe", "version": "saved-build-123",
            "model_file": "E:/saved/fixture-UD-Q3_K_XL.gguf", "thinking": "off",
            "profile_identity": {"base_url": "http://127.0.0.1:8087/v1", "model": "saved-64k",
                                 "context": 65536, "output": 8192},
            "arguments": ["--private-option", "NEVER-COPY-RAW-ARGUMENTS"],
        }
        self.write("evidence/server-manifest.json", self.manifest)
        self.write("evidence/runtime-model-bytes.json", {
            "captured_utc": "2026-10-19T03:05:48Z", "files": [
                {"kind": kind, "resolved_path": path, "size_bytes": 123,
                 "sha256": digest * 64, "pre_post_stat_unchanged": True}
                for kind, path, digest in (("model", self.manifest["model_file"], "a"),
                                           ("runtime", self.manifest["executable"], "b"))
            ],
        })
        self.write("evidence/completed-workflow-status.json", {
            "workflow_stage": "completed",
            "quality_result": {"graded": True, "weighted_percent": 57.575757},
            "notes": "NEVER-COPY-ARBITRARY-METADATA",
        })
        self.write("jb/adapt/jb-oc-agg1/config.json", {
            "executor": "local/saved-64k", "improver": "local/saved-64k", "judge": "saved-64k",
            "api_key": "NEVER-COPY-KEY",
        })
        self.summary = {
            "task_id": "technical_writers/task1", "domain": "technical_writers", "k": 1,
            "status": "ok", "modes": {"agg": {"stages": [
                {"label": "dom1", "fell_back": False, "notes": "PRIVATE-RAW-STAGE-NOTES"},
                {"label": "inv1", "fell_back": True, "error": "timeout after 3600s"},
            ]}}, "selections": {"agg": {"chosen_label": "agg1", "experience": {
                "cue": "A task names an external reference without its URL.",
                "lesson": "Find the author's page rather than guessing the URL.",
                "evidence": "An observed contrast in this one task, with confounded guidance.",
                "confidence": "Unproven across tasks.", "edits": "A reference retrieval rule.",
            }}}, "raw_session": "NEVER-COPY-TRANSCRIPT",
        }
        self.write(importer.SUMMARY, self.summary)
        (self.root / ".env").write_text("HARNESS2_LOCAL_MODEL=todays-different-model\n"
                                       "HARNESS2_LOCAL_CONTEXT=12345\n", encoding="utf-8")

    def write(self, relative, value):
        path = self.run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def source_bytes(self):
        return {path.relative_to(self.run).as_posix(): path.read_bytes()
                for path in self.run.rglob("*") if path.is_file()}

    def test_exact_saved_configuration_graph_experience_and_read_only_sources(self):
        before = self.source_bytes()
        with patch.object(importer, "capture_configuration", side_effect=AssertionError(
            "Historical import cannot substitute current setup or current file bytes"
        )):
            report = importer.import_owen(self.root, self.store)
        self.assertTrue(report["created"])
        profile = self.store.get("owen")
        self.assertEqual(len(profile["configurations"]), 1)
        config = profile["configurations"][0]["snapshot"]
        self.assertEqual(config["settings"]["local_context"], "65536")
        self.assertEqual(config["settings"]["local_model"], "saved-64k")
        self.assertEqual(config["settings"]["local_runtime"], "external")
        self.assertEqual(config["identity"]["weights"]["sha256"], "a" * 64)
        self.assertEqual(config["identity"]["runtime"]["version"], "saved-build-123")
        revisions = {row["id"]: row for row in profile["revisions"]}
        self.assertEqual(len(revisions), 3)
        baseline = revisions[report["baseline_revision_id"]]
        domain = revisions[report["domain_revision_id"]]
        final = revisions[report["revision_id"]]
        self.assertEqual(baseline["tree"], self.baseline)
        self.assertEqual(domain["tree"], self.domain)
        self.assertEqual(final["tree"], self.domain)
        self.assertEqual(domain["parents"], [baseline["id"]])
        self.assertEqual(final["parents"], [baseline["id"], domain["id"]])
        self.assertEqual(final["experience"], self.summary["selections"]["agg"]["experience"])
        self.assertEqual(profile["active_configuration_id"], report["configuration_id"])
        self.assertEqual(profile["active_revision_id"], final["id"])
        self.assertEqual(len(profile["runs"]), 1)
        self.assertEqual(Path(profile["runs"][0]["output_dir"]).resolve(), self.run.resolve())
        self.assertTrue(any("57.58%" in note["text"] for note in profile["notes"]))
        self.assertTrue(any(note["evidence_status"] == "suspected" for note in profile["notes"]))
        for path in final["evidence"]:
            self.assertEqual(path["sha256"], hashlib.sha256(
                (self.root / path["path"]).read_bytes()).hexdigest())
        encoded = json.dumps(profile)
        for private in ("NEVER-COPY", "PRIVATE-RAW-STAGE-NOTES", "todays-different-model"):
            self.assertNotIn(private, encoded)
        self.assertNotIn("57.58", json.dumps(final["tree"]) + json.dumps(final["experience"]))
        self.assertEqual(before, self.source_bytes())

    def test_repeated_import_adds_no_events_and_keeps_later_active_state(self):
        report = importer.import_owen(self.root, self.store)
        self.store.activate("owen", revision_id=report["baseline_revision_id"],
                            why="A later experiment returned to baseline.")
        before = self.store.get("owen")
        second = importer.import_owen(self.root, self.store)
        after = self.store.get("owen")
        self.assertFalse(second["created"])
        self.assertTrue(second["active_selection_preserved"])
        self.assertEqual(before, after)

    def test_missing_historical_hashes_remain_unknown_not_recomputed_from_current_files(self):
        (self.run / "evidence/runtime-model-bytes.json").unlink()
        report = importer.import_owen(self.root, self.store)
        snapshot = self.store.get("owen")["configurations"][0]["snapshot"]
        self.assertIsNone(snapshot["identity"]["weights"]["sha256"])
        self.assertIsNone(snapshot["identity"]["runtime"]["sha256"])
        self.assertEqual(snapshot["identity"]["runtime"]["version"], "saved-build-123")
        self.assertTrue(report["warnings"])

    def test_foreign_saved_hash_is_not_attached_to_the_historical_model_path(self):
        path = self.run / "evidence/runtime-model-bytes.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["files"][0]["resolved_path"] = "E:/other-model/other.gguf"
        self.write("evidence/runtime-model-bytes.json", value)
        report = importer.import_owen(self.root, self.store)
        weights = self.store.get("owen")["configurations"][0]["snapshot"]["identity"]["weights"]
        self.assertEqual(weights["path"], self.manifest["model_file"])
        self.assertEqual(weights["status"], "unknown")
        self.assertIsNone(weights["sha256"])
        self.assertIsNone(weights["size_bytes"])
        self.assertTrue(report["warnings"])

    def test_conflicting_saved_profile_identities_are_rejected_before_state_creation(self):
        self.write("jb/adapt/jb-oc-agg1/config.json", {
            "local_profile": {**self.manifest["profile_identity"], "context": 32768},
        })
        with self.assertRaisesRegex(ValueError, "disagrees"):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])

    def test_traversal_and_recognized_grader_marker_are_rejected_before_profile_creation(self):
        self.summary["selections"]["agg"]["chosen_label"] = "../../outside"
        self.write(importer.SUMMARY, self.summary)
        with self.assertRaisesRegex(ValueError, "label"):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])
        self.summary["selections"]["agg"]["chosen_label"] = "agg1"
        self.write(importer.SUMMARY, self.summary)
        (self.harness_root / "agg1/guardrails.md").write_text(
            'deterministic_eval_summary: {"passed_count": 40}\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])

    def test_incomplete_or_malformed_selected_metadata_is_not_presented_as_a_completed_chapter(self):
        self.write("evidence/completed-workflow-status.json", {"workflow_stage": "running"})
        with self.assertRaisesRegex(ValueError, "completed"):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])
        self.write("evidence/completed-workflow-status.json", {"workflow_stage": "completed"})
        self.summary["selections"] = ["not a mapping"]
        self.write(importer.SUMMARY, self.summary)
        with self.assertRaises(ValueError):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])

    def test_linked_source_is_refused_before_profile_creation(self):
        original = self.harness_root / "agg1/guardrails.md"
        outside = self.root / "outside-source.md"
        outside.write_text("Outside content", encoding="utf-8")
        original.unlink()
        try:
            original.symlink_to(outside)
        except OSError:
            self.skipTest("This account cannot create filesystem symlinks")
        with self.assertRaisesRegex(ValueError, "linked"):
            importer.import_owen(self.root, self.store)
        self.assertEqual(self.store.list(), [])


if __name__ == "__main__":
    unittest.main()
