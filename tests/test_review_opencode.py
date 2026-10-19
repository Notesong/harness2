"""OpenCode workspace cleanup must not reuse stale task state."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config, processes
from harness2.benchmarks import jb_opencode_runner as runner


class OpenCodeCleanupReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="opencode cleanup ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.task = self.root / "tasks/finance/task1/task_folder"
        self.task.mkdir(parents=True)
        (self.task / "instructions.txt").write_text("original task", encoding="utf-8")
        self.render = self.root / "render"
        (self.render / ".opencode").mkdir(parents=True)
        (self.render / ".opencode/fixture.json").write_text("{}", encoding="utf-8")
        self.base = self.root / "scratch"
        self.workspace = self.base / f"finance_task1_review_{os.getpid()}"
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.cfg = runner.Settings(
            task_ids=("finance/task1",), label="review", render_dir=self.render,
            tasks_base=self.root / "tasks", opencode_dir=self.root, model_id="fixture/model",
            agent_name="", variant="", agent_steps="", timeout_per_task="1",
            max_retries=2, log_dir=self.logs,
        )
        temporary_base = patch.object(runner, "TEMP_BASE", self.base)
        temporary_base.start()
        self.addCleanup(temporary_base.stop)

    def read_only(self, path):
        portable = config.long_path(path)
        portable.parent.mkdir(parents=True, exist_ok=True)
        portable.write_text("stale", encoding="utf-8")
        portable.chmod(stat.S_IREAD)
        self.assertTrue(portable.is_file())

    def test_cleanup_child_disappearance_is_fatal_while_stale_root_remains(self):
        stale = self.workspace / "stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("must not survive reset", encoding="utf-8")
        with patch.object(processes.shutil, "rmtree", side_effect=FileNotFoundError(
                "fixture child disappeared")):
            with self.assertRaisesRegex(FileNotFoundError, "child disappeared"):
                processes.remove_tree(self.workspace)
            processes.remove_tree(self.workspace, ignore_errors=True)
        self.assertEqual(stale.read_text(encoding="utf-8"), "must not survive reset")

    def test_cleanup_tolerates_an_already_absent_root(self):
        self.assertFalse(self.workspace.exists())
        with patch.object(processes.shutil, "rmtree", side_effect=FileNotFoundError(
                "fixture root already absent")):
            processes.remove_tree(self.workspace)
        self.assertFalse(self.workspace.exists())

    def test_stage_clears_read_only_deep_stale_tree_before_copying(self):
        stale = self.workspace.joinpath(*(["long nested folder " * 2] * 8), "stale.txt")
        self.read_only(stale)
        runner._stage(self.task, self.render, self.workspace)
        self.assertEqual({item.name for item in self.workspace.iterdir()},
                         {"task_folder", ".opencode", "output"})
        self.assertEqual((self.workspace / "task_folder/instructions.txt").read_text(
            encoding="utf-8"), "original task")

    def test_reset_clears_read_only_task_and_output_but_preserves_harness(self):
        runner._stage(self.task, self.render, self.workspace)
        self.read_only(self.workspace / "task_folder/agent-state.txt")
        self.read_only(self.workspace / "output/stale.txt")
        runner._reset_task(self.task, self.workspace)
        self.assertEqual({path.name for path in (self.workspace / "task_folder").iterdir()},
                         {"instructions.txt"})
        self.assertEqual(list((self.workspace / "output").iterdir()), [])
        self.assertTrue((self.workspace / ".opencode/fixture.json").is_file())

    def test_failed_initial_cleanup_never_stages_or_runs_stale_workspace(self):
        stale = self.workspace / "output/stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("must not be delivered", encoding="utf-8")
        with patch.object(runner, "remove_tree", side_effect=PermissionError("fixture busy")), \
                patch.object(runner.shutil, "copytree") as copy, \
                patch.object(runner, "_attempt") as attempt:
            with self.assertRaises(PermissionError):
                runner.run_one(self.cfg, "finance/task1")
        copy.assert_not_called()
        attempt.assert_not_called()
        self.assertEqual(stale.read_text(encoding="utf-8"), "must not be delivered")
        self.assertFalse((self.task.parent / "model_output/review").exists())

    def test_failed_retry_reset_prevents_a_second_attempt(self):
        def cleanup(path):
            if path == self.workspace / "output":
                raise PermissionError("fixture retry reset blocked")
            processes.remove_tree(path)

        with patch.object(runner, "remove_tree", side_effect=cleanup), \
                patch.object(runner, "_attempt", return_value=1) as attempt:
            with self.assertRaisesRegex(PermissionError, "retry reset blocked"):
                runner.run_one(self.cfg, "finance/task1")
        attempt.assert_called_once()
        self.assertFalse((self.task.parent / "model_output/review").exists())
        processes.remove_tree(self.workspace)

    def test_unexpected_runner_error_preserves_partial_scratch_output(self):
        def attempt(*args):
            (self.workspace / "output/partial.txt").write_text("recoverable", encoding="utf-8")
            raise RuntimeError("fixture runner interrupted")

        with patch.object(runner, "_attempt", side_effect=attempt):
            with self.assertRaisesRegex(RuntimeError, "runner interrupted"):
                runner.run_one(self.cfg, "finance/task1")
        self.assertEqual((self.workspace / "output/partial.txt").read_text(
            encoding="utf-8"), "recoverable")
        self.assertFalse((self.task.parent / "model_output/review").exists())
        processes.remove_tree(self.workspace)

    def test_final_cleanup_failure_is_logged_without_losing_delivered_output(self):
        def cleanup(path):
            if path == self.workspace and (self.workspace / "cleanup-blocked").exists():
                raise PermissionError("fixture final cleanup blocked")
            processes.remove_tree(path)

        def attempt(*args):
            self.read_only(self.workspace / "task_folder/agent-state.txt")
            (self.workspace / "output/result.txt").write_text("delivered", encoding="utf-8")
            (self.workspace / "cleanup-blocked").touch()
            return 0

        with patch.object(runner, "remove_tree", side_effect=cleanup), \
                patch.object(runner, "_attempt", side_effect=attempt):
            self.assertTrue(runner.run_one(self.cfg, "finance/task1"))
        self.assertEqual((self.task.parent / "model_output/review/result.txt").read_text(
            encoding="utf-8"), "delivered")
        log = next(self.logs.glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("WARNING scratch cleanup failed", log)
        self.assertIn("fixture final cleanup blocked", log)
        processes.remove_tree(self.workspace)

    def partial_attempt_log(self, code):
        def attempt(*args):
            (self.workspace / "output/partial.txt").write_text("partial", encoding="utf-8")
            return code

        with patch.object(runner, "_attempt", side_effect=attempt) as invoke:
            self.assertTrue(runner.run_one(self.cfg, "finance/task1"))
        invoke.assert_called_once()
        self.assertEqual((self.task.parent / "model_output/review/partial.txt").read_text(
            encoding="utf-8"), "partial")
        return next(self.logs.glob("*.log")).read_text(encoding="utf-8")

    def test_timeout_with_partial_output_is_delivered_once_and_logged_honestly(self):
        log = self.partial_attempt_log(124)
        self.assertIn("exit=124 output=present", log)
        self.assertIn("TIMEOUT finance/task1 attempt 1", log)

    def test_killed_attempt_with_partial_output_keeps_timeout_marker(self):
        log = self.partial_attempt_log(137)
        self.assertIn("exit=137 output=present", log)
        self.assertIn("TIMEOUT finance/task1 attempt 1", log)

    def test_nonzero_attempt_with_partial_output_records_exit_without_timeout(self):
        log = self.partial_attempt_log(2)
        self.assertIn("exit=2 output=present", log)
        self.assertNotIn("TIMEOUT", log)

    def test_timeout_without_output_retries_and_records_both_attempts(self):
        def attempt(*args):
            if args[-1] == 1:
                return 124
            (self.workspace / "output/result.txt").write_text("delivered", encoding="utf-8")
            return 0

        with patch.object(runner, "_attempt", side_effect=attempt) as invoke:
            self.assertTrue(runner.run_one(self.cfg, "finance/task1"))
        self.assertEqual(invoke.call_count, 2)
        log = next(self.logs.glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("attempt 1: exit=124 output=absent", log)
        self.assertIn("TIMEOUT finance/task1 attempt 1", log)
        self.assertIn("attempt 2: exit=0 output=present", log)

    def test_success_cleans_read_only_agent_files_after_delivering_output(self):
        def attempt(*args):
            self.read_only(self.workspace / "task_folder/agent-state.txt")
            (self.workspace / "output/result.txt").write_text("delivered", encoding="utf-8")
            return 0

        with patch.object(runner, "_attempt", side_effect=attempt):
            self.assertTrue(runner.run_one(self.cfg, "finance/task1"))
        self.assertFalse(self.workspace.exists())
        self.assertEqual((self.task.parent / "model_output/review/result.txt").read_text(
            encoding="utf-8"), "delivered")


if __name__ == "__main__":
    unittest.main()
