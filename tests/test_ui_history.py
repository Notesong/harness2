"""Read-only CLI history discovery, including the recovered local workflow shape."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness2.ui import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="history résumé ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.workspace = SimpleNamespace(runs_root=self.runs)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def task(self, output, *, namespace="jb-oc-agg1", task="technical_writers__task1",
             status="ok", mode="agg"):
        return self.write(output / "jb/adapt" / namespace / task / "summary.json", {
            "task_id": task.replace("__", "/"), "domain": "technical_writers", "k": 1,
            "status": status, "modes": {mode: {}}, "notes": "PRIVATE-NOTES",
            "selections": {mode: {"chosen_run_dir": str(self.root / "outside")}},
        })

    def workflow(self, output, *, code=2, ended=True):
        data = {"started_utc": "2026-10-19T03:02:19Z", "driver_pid": 1234,
                "all_stage_exit_codes_zero": code == 0,
                "profile": {"base_url": "http://127.0.0.1:8087/v1", "model": "fixture-model",
                            "context": 65536, "output": 8192},
                "stages": [{"name": "sweep", "exit_code": 0, "notes": "PRIVATE-NOTES"},
                           {"name": "judge", "exit_code": code,
                            "arguments": ["--api-key", "PRIVATE-KEY"]}],
                "notes": "PRIVATE-NOTES", "environment": {"API_KEY": "PRIVATE-KEY"}}
        if ended:
            data["ended_utc"] = "2026-10-19T07:45:50Z"
        self.write(output / "evidence/workflow-status.json", data)

    def test_recovered_completion_replaces_original_judge_failure_without_raw_evidence(self):
        output = self.runs / "local-model-64k"
        summary = self.task(output)
        self.workflow(output)
        self.write(summary.parent.parent / "config.json", {
            "executor": "local/fixture-model", "improver": "local/fixture-model",
            "judge": "fixture-model", "project": "", "location": "global",
            "local_profile": {"base_url": "http://127.0.0.1:8087/v1", "model": "fixture-model",
                              "context": 65536, "output": 8192},
            "api_key": "PRIVATE-KEY", "max_tasks": 1,
        })
        self.write(output / "evidence/unattended-follow-through/status.json", {
            "status": "completed", "all_recovery_stages_succeeded": True,
            "ended_utc": "2026-10-19T08:02:42Z",
            "stages": {"judge": {"exit_code": 0, "notes": "PRIVATE-NOTES"},
                       "report": {"exit_code": 0}},
        })
        before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
        records = history.discover_history(self.workspace)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["phase"], "Complete")
        self.assertTrue(record["imported"])
        self.assertEqual(record["finished_at"], "2026-10-19T08:02:42+00:00")
        self.assertEqual(record["started_at"], "2026-10-19T03:02:19+00:00")
        self.assertEqual(record["progress"], {"done": 1, "failed": 0, "total": 1})
        self.assertEqual(record["models"]["local_context"], "65536")
        self.assertEqual(record["models"]["local_output"], "8192")
        self.assertTrue(all(phase["status"] == "succeeded" for phase in record["phases"]))
        encoded = json.dumps(record)
        for private in ("PRIVATE-NOTES", "PRIVATE-KEY", "driver_pid", "arguments", "chosen_run_dir"):
            self.assertNotIn(private, encoded)
        self.assertEqual(before, {path: path.read_bytes() for path in output.rglob("*")
                                  if path.is_file()})

    def test_unrecovered_failure_and_unknown_running_process_are_never_live(self):
        failed = self.runs / "failed"
        self.task(failed)
        self.workflow(failed)
        interrupted = self.runs / "original-running"
        self.task(interrupted, status="partial")
        self.workflow(interrupted, code=None, ended=False)
        records = {Path(row["output_dir"]).name: row for row in history.discover_history(self.workspace)}
        self.assertEqual(records["failed"]["status"], "failed")
        self.assertEqual(records["original-running"]["status"], "interrupted")
        self.assertIsNone(records["original-running"]["finished_at"])
        self.assertFalse(any(row["status"] in ("running", "starting") for row in records.values()))

    def test_manual_capacity_stop_without_summary_does_not_invent_completed_work(self):
        output = self.runs / "aborted-32k"
        self.write(output / "manual-stop.json", {
            "status": "manually_stopped_for_capacity_adjustment", "stopped_utc": "2026-10-19T02:57:25Z",
            "baseline_completed": False, "adaptation_completed": False,
        })
        self.write(output / "first-profile-sanitized.json", {
            "recorded_run_identity": {"executor": "local/old-model",
                                      "local_profile": {"model": "old-model", "context": 32768,
                                                        "output": 4096}},
        })
        record, = history.discover_history(self.workspace)
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["progress"]["done"], 0)
        self.assertEqual(record["models"]["local_context"], "32768")

    def test_multiple_summaries_share_stable_root_id_and_orphaned_ui_children_are_separate(self):
        output = self.runs / "cli"
        self.task(output)
        self.task(output, task="technical_writers__task2")
        self.task(self.runs / "ui/first-orphan")
        self.task(self.runs / "ui/second-orphan")
        first = history.discover_history(self.workspace)
        self.assertEqual(len(first), 3)
        self.assertEqual(len({row["id"] for row in first}), 3)
        self.assertTrue(all(len(row["id"]) == 12 for row in first))
        self.assertNotIn(str((self.runs / "ui").resolve()), [row["output_dir"] for row in first])
        self.assertEqual(next(row for row in first if row["output_dir"] == str(output.resolve()))[
            "progress"], {"done": 2, "failed": 0, "total": 2})
        second = history.discover_history(SimpleNamespace(runs_root=self.runs / "../runs"))
        self.assertEqual({row["output_dir"]: row["id"] for row in first},
                         {row["output_dir"]: row["id"] for row in second})

    def test_demo_report_and_current_status_supported_without_inventing_tasks(self):
        self.write(self.runs / "demo/demo.json", {"n_tasks": 2, "done": 2, "failed": 0,
                                                 "tasks": [{"task": "one", "status": "ok"}]})
        self.write(self.runs / "early/current.json", {"status": "running",
                                                      "started_at": "2026-10-19T00:00:00Z"})
        self.write(self.runs / "irrelevant/current.json", {"api_key": "do-not-import"})
        records = {Path(row["output_dir"]).name: row for row in history.discover_history(self.workspace)}
        self.assertEqual(set(records), {"demo", "early"})
        self.assertEqual(records["demo"]["kind"], "demo")
        self.assertEqual(records["demo"]["progress"], {"done": 2, "failed": 0, "total": 2})
        self.assertEqual(records["early"]["status"], "interrupted")
        self.assertEqual(records["early"]["total_tasks"], 0)

    def test_workbuddy_namespace_preserves_subset_and_codex_executor(self):
        output = self.runs / "workbuddy"
        self.task(output, namespace="wb-office-cx-seq2", mode="seq")
        record, = history.discover_history(self.workspace)
        self.assertEqual(record["params"]["benchmark"], "wb")
        self.assertEqual(record["params"]["subset"], "office")
        self.assertEqual(record["params"]["substrate"], "cx")
        self.assertEqual(record["params"]["mode"], "sequential")

    def test_symlink_roots_and_metadata_never_read_outside_runs(self):
        outside = self.root / "external"
        self.task(outside)

        def directory_link(destination, target):
            try:
                destination.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                if os.name != "nt":
                    self.skipTest(f"Host does not permit fixture symlinks: {exc}")
                # Windows directory junctions need no symlink privilege and are
                # also explicitly rejected by discovery. All targets are inside
                # this test's verified TemporaryDirectory, outside its runs root.
                command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/cmd.exe"
                result = subprocess.run([str(command), "/d", "/c", "mklink", "/J",
                                         str(destination), str(target)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

        directory_link(self.runs / "linked-root", outside)
        output = self.runs / "linked-summary/jb/adapt"
        output.mkdir(parents=True)
        directory_link(output / "jb-oc-agg1", outside / "jb/adapt/jb-oc-agg1")
        original = Path.open

        def guarded(path, *args, **kwargs):
            self.assertTrue(path.resolve().is_relative_to(self.runs.resolve()))
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", guarded):
            self.assertEqual(history.discover_history(self.workspace), [])

    def test_malformed_oversized_and_irrelevant_evidence_are_ignored(self):
        output = self.runs / "bad"
        summary = output / "jb/adapt/jb-oc-agg1/task/summary.json"
        summary.parent.mkdir(parents=True)
        summary.write_text("not json", encoding="utf-8")
        self.write(self.runs / "notes-only/evidence/stages.json", {"notes": "PRIVATE-NOTES"})
        self.assertEqual(history.discover_history(self.workspace), [])
        summary.write_bytes(b" " * (history.MAX_JSON_BYTES + 1))
        self.assertEqual(history.discover_history(self.workspace), [])

    def test_root_enumeration_and_summary_walk_are_bounded(self):
        for index in range(8):
            self.task(self.runs / f"run-{index}")
        with patch.object(history, "MAX_ENTRIES", 3):
            self.assertLessEqual(len(history.discover_history(self.workspace)), 3)
        output = self.runs / "many"
        for index in range(12):
            self.task(output, task=f"task-{index}")
        with patch.object(history, "MAX_ENTRIES", 5):
            self.assertLessEqual(len(history._summaries(output, self.runs.resolve())), 5)


if __name__ == "__main__":
    unittest.main()
